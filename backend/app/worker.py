"""独立 Worker 进程（P3/T13）。

    python -m app.worker --once            # 跑一轮就退出（测试与 cron 用）
    python -m app.worker --interval 5      # 常驻，每 5 秒扫一次

## 为什么要有独立进程

长任务跑在 API 进程里有两个问题：

1. **API 重启会连坐任务**。改一行代码重启服务，正在跑的生产线就断了。
2. **无法多进程扩容**。SQLite 是单写者，但"领取任务"这一步可以在多个
   Worker 间用租约协调——只要每个任务同一时刻只有一个持有者。

所以任务表 + 租约 + fencing_token 三者配套：任务是数据，不是内存里的调用栈。

## 单进程内并发度 = 1

SQLite 单写者，并发写会 `database is locked`。这里刻意不并发，
靠"多起几个 Worker 进程 + 租约协调"来横向扩展，而不是在一个进程里开线程池。
少一层并发就少一类难查的 bug。

## 本 Worker 不做的事

- 不自动批准、不自动发布
- 不自动重发结果未知的调用（那可能双倍计费）
- 不自动删除孤儿文件
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .core.config import get_settings
from .core.errors import TaskStopped
from .models.entities import Base, Job, Run, ContentItem, enable_sqlite_fk
from .services.production_service import ProductionService
from .services.content_lifecycle import update_content_state
from .services.profile_store import ProfileStore
from .services.provider_contract import ProviderStore, RunMode, SecretStore
from .services.provider_runtime import ProviderRuntime
from .services.recovery_service import RecoveryService

STOP = False


def _sigterm(_signum, _frame):  # pragma: no cover
    global STOP
    STOP = True


def build_session_factory():
    s = get_settings()
    s.ensure_dirs()
    engine = create_engine(s.database_url, future=True)
    enable_sqlite_fk(engine)
    Base.metadata.create_all(engine)
    return s, sessionmaker(bind=engine, future=True)


def heartbeat(settings, worker_id: str, extra: dict | None = None) -> None:
    """写心跳文件。API 的 /health 读它判断 Worker 是否在跑。"""
    path = settings.storage_root / "worker.heartbeat"
    path.write_text(
        json.dumps({
            "worker_id": worker_id,
            "pid": os.getpid(),
            "instance_id": os.environ.get("CWB_INSTANCE_ID"),
            "at": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }, ensure_ascii=False),
        encoding="utf-8",
    )


class Worker:
    def __init__(self, session_factory, settings, *, worker_id: str = "worker-1",
                 actor: str = "coisini") -> None:
        self.sf = session_factory
        self.settings = settings
        self.worker_id = worker_id
        self.actor = actor
        self.recovery = RecoveryService(session_factory, actor=actor,
                                        worker_id=worker_id)
        runtime = ProviderRuntime(
            ProviderStore(settings.storage_root / "provider_configs.json"),
            SecretStore(settings.secret_store_path),
        )
        self.production = ProductionService(session_factory, actor=actor,
                                            runtime=runtime,
                                            profiles=ProfileStore())

    # ------------------------------------------------------------ 一轮

    def tick(self) -> dict:
        """扫描 → 执行 → 汇报。返回本轮做了什么。"""
        report = self.recovery.scan()
        out = {
            "worker_id": self.worker_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "recovered": len(report.auto_fixed),
            "needs_manual": len(report.needs_manual),
            "processed": 0,
            "note": None,
        }
        heartbeat(self.settings, self.worker_id,
                  {"recovered": out["recovered"], "needs_manual": out["needs_manual"]})
        try:
            from .services.douyin_scheduler import dispatch_due
            out['platform_revisit_dispatched'] = dispatch_due(self.sf,self.settings)
        except Exception:
            out['platform_revisit_dispatched'] = False

        # 取一个待办 job
        job = self._claim_next()
        if job is None:
            from .services.video_service import VideoService
            if not hasattr(self,'videos'):
                self.videos=VideoService(self.settings)
            out['video_processed']=self.videos.process_next(heartbeat=lambda:heartbeat(self.settings,self.worker_id,{'active_stage':'video'}))
            out["note"] = "无待办 job"
            return out

        try:
            out["processed"] = 1
            result = self._run_job(job)
            self.recovery.release(job["job_id"], job["fencing_token"],
                                  state=result.get("stopped") or ("failed" if result.get("partial") else "succeeded"), refs=result)
        except Exception as exc:  # noqa: BLE001 — Worker 必须活着，不能因单任务崩掉
            with self.sf() as s:
                preserved = s.get(Job, job["job_id"]).output_refs
            self.recovery.release(job["job_id"], job["fencing_token"], state="failed",
                                  refs=preserved, error=f"{type(exc).__name__}: {exc}")
            out["note"] = f"job {job.get('stage')} 失败：{exc}"
        return out

    def _claim_next(self) -> dict | None:
        """找最早的一个 queued job 并领走。

        这里用 `with_for_update` 的语义（SQLite 无行锁，靠租约兜底）：
        真正保证唯一持有者是 `acquire()` 里的 token 校验，不是这里的查询。
        """
        with self.sf() as s:
            job = (
                s.query(Job).filter(Job.state == "queued")
                .order_by(Job.started_at.is_(None).desc(), Job.started_at).first()
            )
            if job is None:
                # 没有 queued 的，再看有没有卡住的
                rows = s.query(Job).filter(Job.state == "running").all()
                if not rows:
                    return None
                return None
            jid = job.id
        try:
            return self.recovery.acquire(jid)
        except Exception:
            return None

    def _run_job(self, job: dict) -> dict:
        """执行一个 job。

        P3 阶段 job 的执行体仍由 ProductionService 承担：Worker 负责
        "什么时候跑、谁有权跑"，ProductionService 负责"跑什么"。
        真正的阶段级分发（只重跑失败阶段）在 P4 细化。
        """
        with self.sf() as s:
            row = s.get(Job, job["job_id"])
            request = dict((row.output_refs or {}).get("request") or {})
            run_id = row.run_id
        if job.get("stage") != "batch_dispatch" or not request:
            raise ValueError("任务缺少可执行请求，请通过内容生产页创建任务")
        request["run_mode"] = RunMode(request["run_mode"])
        request["platforms"] = tuple(request["platforms"])
        def check_control():
            with self.sf() as s:
                row = s.get(Job, job["job_id"])
                if row.state in {"paused", "cancelled"}:
                    raise TaskStopped(row.state)
        self.production.check_control = check_control
        self.production.runtime.before_call = check_control
        done = threading.Event()
        def keep_alive():
            while not done.wait(15):
                self.recovery.renew(job["job_id"], job["fencing_token"])
                heartbeat(self.settings, self.worker_id, {"active_job": job["job_id"]})
        thread = threading.Thread(target=keep_alive, daemon=True)
        thread.start()
        try:
            operation = request.pop('operation', 'produce')
            if operation == 'illustrate':
                result = self.production.illustrate(**request, run_id=run_id)
                request['operation'] = operation
            else:
                result = self.production.produce(**request, run_id=run_id)
            result["request"] = {**request, "run_mode": request["run_mode"].value}
            if result.get("paused"):
                result["stopped"] = "paused"
                result["request"] = {**request, "run_mode": request["run_mode"].value}
            return result
        except TaskStopped as exc:
            with self.sf() as s:
                run=s.get(Run, run_id);run.state=exc.state
                for stage_job in s.query(Job).filter_by(run_id=run_id,state='running'):
                    if stage_job.stage!='batch_dispatch':
                        stage_job.state=exc.state;stage_job.finished_at=datetime.now(timezone.utc)
                item=s.get(ContentItem,run.content_id) if run.content_id else None
                if item and not item.active_revision_id and exc.state=='cancelled':update_content_state(s,item,'blocked')
                s.commit()
            return {"request": {**request, "run_mode": request["run_mode"].value}, "stopped": exc.state}
        except Exception as exc:
            with self.sf() as s:
                run = s.get(Run, run_id)
                run.state = "failed"
                run.error = str(exc)
                run.blocked_stage = "execution"
                for stage_job in s.query(Job).filter_by(run_id=run_id, state="running").all():
                    if stage_job.stage != "batch_dispatch":
                        stage_job.state = "failed"
                        stage_job.error = str(exc)
                s.commit()
            raise
        finally:
            done.set()
            thread.join(timeout=2)
            self.production.check_control = None
            self.production.runtime.before_call = None

    # ------------------------------------------------------------ 常驻

    def run_forever(self, interval: float = 5.0, max_ticks: int | None = None) -> None:
        ticks = 0
        while not STOP:
            res = self.tick()
            print(json.dumps(res, ensure_ascii=False), flush=True)
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            if STOP:
                break
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="内容工作台独立 Worker")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出")
    ap.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒数")
    ap.add_argument("--ticks", type=int, default=None, help="最多跑几轮（默认无限）")
    ap.add_argument("--worker-id", default="worker-1")
    args = ap.parse_args(argv)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    settings, sf = build_session_factory()
    w = Worker(sf, settings, worker_id=args.worker_id)
    print(f"[worker] {args.worker_id} 启动，interval={args.interval}s", flush=True)

    if args.once:
        print(json.dumps(w.tick(), ensure_ascii=False), flush=True)
        return 0
    w.run_forever(interval=args.interval, max_ticks=args.ticks)
    print("[worker] 已停止", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

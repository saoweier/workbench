"""启动脚本的状态汇报（P5/T21）。

单独成文件的原因：这段逻辑原本以内嵌 heredoc 的形式写在 start.sh 里，
f-string 里的引号要层层转义，写错时报错信息指向一大坨 shell 上下文，
非常难排查。独立成 .py 后可以直接跑、直接 lint。

放 scripts/ 而不是 backend/app/：这是**运维脚本**的一部分，
不是应用代码，不该被 API 依赖，也不该进 app 包的导入路径。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

WORKER_LABEL = {"running": "运行中", "stale": "心跳过期",
                "not_running": "未运行", "unknown": "状态未知"}


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "8000"
    url = f"http://127.0.0.1:{port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            h = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"  ! 无法读取健康状态：{exc}")
        return 1

    w = h.get("worker") or {}
    st = h.get("storage") or {}
    ws = WORKER_LABEL.get(w.get("status"), w.get("status") or "未知")
    wid = f"（{w['worker_id']}）" if w.get("worker_id") else ""

    print(f"  API      : {h.get('api', {}).get('status')}  v{h.get('api', {}).get('version')}")
    print(f"  Worker   : {ws}{wid}")
    print(f"  存储     : {st.get('status')}  可写={st.get('artifact_dir_writable')}")
    money = "已设" if h.get("money_limit_set") else "未设（null ≠ 零额度）"
    print(f"  成本模式 : {h.get('cost_mode')}  金额上限：{money}")
    if w.get("note"):
        print(f"  说明     : {w['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

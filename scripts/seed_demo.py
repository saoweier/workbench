"""Generate six explicit offline demonstration packages, without external calls."""
from pathlib import Path
import argparse
import copy
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
TOPICS = [
    ("自动制作的人工边界", ["先确定受众与内容范围", "流程连续制作双平台稿", "只在最终图片集中审核", "批准后下载独立发布包", "发布操作由本人完成"]),
    ("一条主张怎样找到来源", ["把原始资料登记成来源", "每条观点关联来源编号", "把计划和事实分别标注", "搜索摘要保留依据限制", "资料不足先缩小范围"]),
    ("成品审批与版本留存", ["文案与图片绑定同一版本", "逐平台预览最终页图", "修改要求保留原稿历史", "新图片需要重新批准", "导出清单记录文件指纹"]),
    ("看懂模型的用量记录", ["填写接口后主动开始生成", "一次请求登记一次用量", "未知价格保留为空值", "超时结果先查询再处理", "需要时设置硬金额上限"]),
    ("从数据回填到可靠复盘", ["登记作品链接和发布时间", "保留指标时间与统计口径", "缺失数值不替换成零", "重复导入复用原快照", "区分观察和解释及实验"]),
    ("下一轮调整如何回退", ["把复盘关联到下一轮建议", "给每项调整记录证据", "小幅修改保留采用理由", "大幅变更等待人工决定", "回退后保留完整历史"]),
]

def create_sources():
    base = json.loads((ROOT / "examples/C001/seeds/C001/seed.json").read_text(encoding="utf-8"))
    demo = ROOT / "examples/demo"
    demo.mkdir(parents=True, exist_ok=True)
    head = "# 演示资料\n\n以下是工作流说明与虚构演练数据，没有实际平台成效。\n"
    sections = {}
    for n, (title, points) in enumerate(TOPICS, 1):
        did = f"DEMO-{n:03d}"
        sections[did] = f"\n## {did}\n" + "\n".join(points) + "\n"
    notes = head + "".join(sections[f"DEMO-{n:03d}"] for n in range(1, len(TOPICS) + 1))
    paths = []
    for n, (title, points) in enumerate(TOPICS, 1):
        did = f"DEMO-{n:03d}"
        data = copy.deepcopy(base)
        data.update(display_id=did, topic=f"【演示】{title}", dataset_kind="fixture", run_mode="fixture")
        # 来源必须带可读正文：只声明路径不给正文，真实模式下的证据闸门会
        # （正确地）拒绝批准——演示样本也就无法走通真实发布链路。
        data["sources"] = [{"id":"S01", "kind":"local_planning_document", "path":"../../source-notes.md",
            "locator":did, "access_state":"ok", "excerpt_basis":"local_file", "excerpt":sections[did].strip(),
            "supports":"演示工作流的设计说明", "limitations":"工程示例，不是实际运营结论"}]
        data["claims"] = [{"id":f"C{i:02d}", "kind":"project_plan", "statement":point,
                           "source_ids":["S01"]} for i, point in enumerate(points,1)]
        data["limitations"] = ["全部为离线演示样本；发布链接、指标和评论为虚构数据。"]
        for draft in data["platform_drafts"]:
            draft["title"] = title
            draft["caption"] = "【演示样本】" + "；".join(points) + "。这些内容用于检查工程流程。"
            for i, page in enumerate(draft["pages"]):
                point = points[i % len(points)]
                page.update(heading=title if i == 0 else point, kicker="演示样本",
                    body=[point, "保留步骤输入与结果，便于后续检查。", "遇到不完整材料时明确记录原因。",
                          "通过最终成品检查后继续下一步。"], footnote="离线演示，未真实发布。",
                    claim_ids=[f"C{i % len(points)+1:02d}"])
        path = demo / "seeds" / did / "seed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        paths.append(path)
    (demo / "source-notes.md").write_text(notes, encoding="utf-8")
    return paths

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=ROOT / "examples/demo/storage")
    args = parser.parse_args()
    target = args.target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    os.environ.update(CWB_STORAGE_ROOT=str(target), CWB_DATABASE_URL=f"sqlite:///{target / 'workbench.db'}",
        CWB_ARTIFACT_DIR=str(target / "artifacts"), CWB_TMP_DIR=str(target / "tmp"),
        CWB_SECRET_STORE_PATH=str(target / "secrets.json"))
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    def post(path, body):
        response = client.post("/api/v1" + path, json=body)
        if response.status_code >= 400: raise RuntimeError(f"{path}: {response.text}")
        return response.json()
    existing = {c["display_id"] for c in client.get("/api/v1/contents").json()["items"]}
    results = []
    for index, path in enumerate(create_sources(), 1):
        did = f"DEMO-{index:03d}"
        if did in existing:
            continue
        item = post("/contents/import-seed", {"seed_path":str(path)})
        cid = item["content_id"]
        detail = client.get(f"/api/v1/contents/{cid}").json()
        prs = detail["revisions"][0]["platforms"]
        for pr in prs:
            rendered = post(f"/platform-revisions/{pr['platform_revision_id']}/render", {})
            if not rendered.get("ok"): raise RuntimeError(str(rendered))
        detail = client.get(f"/api/v1/contents/{cid}").json()
        prs = detail["revisions"][0]["platforms"]
        if index in (2,3,5,6):
            for pr in prs if index != 2 else prs[:1]:
                decision = post("/review-decisions", {"platform_revision_id":pr["platform_revision_id"],
                    "decision":"approve", "actor":"coisini", "expected_manifest_hash":pr["manifest_hash"],
                    "note":"预设演示审批，非用户对真实内容的批准"})
                if not decision.get("ok"): raise RuntimeError(str(decision))
        if index == 4:
            post("/review-decisions", {"platform_revision_id":prs[0]["platform_revision_id"],
                "decision":"hold","actor":"coisini","expected_manifest_hash":prs[0]["manifest_hash"]})
        if index in (3,5,6):
            post("/packages", {"platform_revision_ids":[p["platform_revision_id"] for p in prs],"actor":"coisini"})
            for p in prs:
                pid = f"DEMO-{index:03d}-{p['platform']}"
                pub = post("/publications", {"platform_revision_id":p["platform_revision_id"],
                    "platform_post_id":pid,"link":f"https://example.invalid/{pid}","published_at":"2026-10-01T09:00:00+08:00",
                    "run_mode":"fixture","note":"虚构演示登记，无真实平台发布"})
                if index >= 5:
                    csv = "platform,post_id,observed_at,metric_name,value,unit,traffic_type,aggregation_kind\n"
                    for hours, at in [(24,"2026-10-02T09:00:00+08:00"),(48,"2026-10-03T09:00:00+08:00")]:
                        for metric,value in [("views",1200*index*hours//24),("likes",60*index),("comments",8),("shares",12)]:
                            csv += f"{p['platform']},{pid},{at},{metric},{value},count,organic,cumulative\n"
                    post("/imports", {"content":csv,"run_mode":"fixture"})
                    comments = "platform,post_id,anonymous_comment_id,text,observed_at,like_count\n"
                    for j, text in enumerate(["这个流程如何开始？","希望看到完整教程","依据是什么？","我也遇到过这个问题"],1):
                        comments += f"{p['platform']},{pid},demo-{j},{text},2026-10-03T09:00:00+08:00,{j}\n"
                    post("/comments/imports", {"content":comments,"run_mode":"fixture","sampling_method":"虚构演练样本"})
        if index >= 5:
            review = post(f"/contents/{cid}/reviews", {"run_mode":"fixture","next_topics":["【演示】下一轮只改封面"]})
            report_id = review.get("id") or review.get("review_report_id")
            feedback = post("/feedback", {"review_report_id":report_id,"kind":"hook","proposal":"【演示】下一轮只改封面",
                "magnitude":"small","run_mode":"fixture","evidence_refs":[report_id],"previous_value":"原封面","new_value":"提问式封面"})
            if index == 6:
                post(f"/feedback/{feedback['id']}/adopt", {})
                post(f"/feedback/{feedback['id']}/revert", {"reason":"演示回退"})
        results.append({"display_id":did,"content_id":cid})
        print("生成演示样本 " + did, flush=True)
    (target / "demo-manifest.json").write_text(json.dumps({"fixture":True,"samples":results},ensure_ascii=False,indent=2),encoding="utf-8")

if __name__ == "__main__": main()

"""P0 端到端冒烟测试：不打网络、不产生费用，只验证契约行为。

覆盖：
- /health、/seeds 校验、/profiles 可编辑、/provider-configs 密钥掩码、
  保存不触发调用、/usage 金额未知不显示 0、预算策略
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须先隔离存储再 import app：否则会直接用生产库 storage/cwb.db，
# 测试会写坏真实数据（也会因为并发/锁抢到 disk I/O error）。
_TMP = Path(tempfile.mkdtemp(prefix="cwb-p0-"))
os.environ["CWB_STORAGE_ROOT"] = str(_TMP)
os.environ["CWB_ARTIFACT_DIR"] = str(_TMP / "artifacts")
os.environ["CWB_TMP_DIR"] = str(_TMP / "tmp")
os.environ["CWB_DATABASE_URL"] = f"sqlite:///{_TMP / 'p0.db'}"
os.environ["CWB_SECRET_STORE_PATH"] = str(_TMP / "secrets.json")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  → ' + detail) if detail else ''}")


print("\n[1] 健康检查")
r = client.get("/api/v1/health")
check("health 200", r.status_code == 200, str(r.status_code))
h = r.json()
check("worker 状态显式标注未运行", h["worker"]["status"] == "not_running", h["worker"]["status"])
check("health 不返回凭据", "secret" not in json.dumps(h).lower() and "api_key" not in json.dumps(h).lower())
check("存储可写", h["storage"]["artifact_dir_writable"] is True)

print("\n[2] T01 seed 校验")
r = client.get("/api/v1/seeds")
check("seeds 列表 200", r.status_code == 200)
seeds_found = r.json()["seeds"]
check("发现 C001 seed", any(s["display_id"] == "C001" for s in seeds_found), str(seeds_found))

r = client.post("/api/v1/seeds/C001/validate")
check("C001 校验 200", r.status_code == 200)
rep = r.json()
check("C001 校验通过", rep["passed"] is True, rep["summary"])
check("抖音 5 页", rep["report"]["platforms"]["douyin"]["page_count"] == 5)
check("小红书 6 页", rep["report"]["platforms"]["xiaohongshu"]["page_count"] == 6)
check("来源文件全部存在", rep["report"]["sources"]["missing"] == [])
check("平台验证标记为 false", rep["report"]["platforms"]["_render_profile"]["platform_upload_verified"] is False)

print("\n[3] T02 发布规格可配置")
r = client.get("/api/v1/profiles")
check("profiles 200", r.status_code == 200)
d = r.json()
check("含两个平台最新版本", set(d["latest"].keys()) == {"douyin", "xiaohongshu"})
check("带未经核对声明", "platform_upload_verified=false" in d["disclaimer"] or "未经实际发布界面核对" in d["disclaimer"])

r = client.get("/api/v1/profiles/douyin/latest")
orig = r.json()
check("默认尺寸 1080x1440", (orig["render"]["width_px"], orig["render"]["height_px"]) == (1080, 1440))
check("默认 verified=false", orig["platform_upload_verified"] is False)

r = client.post("/api/v1/profiles/douyin/versions", json={
    "name": "editorial-development-default",
    "render": {"width_px": 1242, "height_px": 1660, "format": "png", "font_family": "Noto Sans CJK SC"},
    "limits": {"max_title_chars": 20, "max_caption_chars": 1200, "min_pages": 1, "max_pages": 12},
    "note": "用户自定义测试尺寸",
})
check("新版本创建 201", r.status_code == 201, str(r.status_code))
new = r.json()
check("版本号递增到 2", new["version"] == 2, str(new["version"]))
check("新尺寸生效", new["render"]["width_px"] == 1242)
check("新版本仍 verified=false", new["platform_upload_verified"] is False)
check("新版本来源标 engineering_default", new["source"] == "engineering_default")

r = client.get("/api/v1/profiles/versions/" + orig["id"])
check("旧版本内容未被修改", r.json()["render"]["width_px"] == 1080)
check("旧版本被标记 superseded", r.json()["superseded_by"] == new["id"])
check("新版本为最新", client.get("/api/v1/profiles").json()["latest"]["douyin"] == new["id"])

r = client.post("/api/v1/profiles/douyin/versions", json={
    "limits": {"max_pages": 1, "min_pages": 5}
})
check("非法页数范围被拒 422", r.status_code == 422, str(r.status_code))

print("\n[4] T03 Provider 设置")
r = client.get("/api/v1/provider-configs")
check("列表 200", r.status_code == 200)
d = r.json()
check("未配置时 text 为 null", d["defaults"]["text"] is None)
check("未配置时明确提示可用本地开发", "local_seed" in d["capabilities"]["text"]["note"])
check("搜索未配置时提示须标注未自动搜索", "未执行自动搜索" in d["capabilities"]["search"]["note"])
check("cost_mode 为 usage_tracking", d["cost_mode"] == "usage_tracking")
check("money_limit 未设置", d["money_limit_set"] is False)

r = client.post("/api/v1/provider-configs", json={
    "name": "本地测试端点",
    "kind": "text",
    "adapter_type": "openai_compatible",
    "base_url": "http://127.0.0.1:11434/v1",
    "model_id": "test-model",
    "enabled": True,
    "allow_localhost": True,
    "api_key": "sk-SUPER-SECRET-VALUE-should-never-return",
})
check("创建 201", r.status_code == 201, str(r.status_code))
created = r.json()
check("保存不触发真实调用", created["called_provider"] is False)
cfg = created["item"]
check("响应不含密钥原文", "SUPER-SECRET" not in json.dumps(created))
check("响应有 secret_configured", cfg["secret_configured"] is True)
check("响应有掩码", cfg["secret_mask"] == "••••••")
check("secret_ref 不外泄", "secret_ref" not in cfg)
check("状态为已保存未测", cfg["status"] == "configured_untested", cfg["status"])

r = client.post(f"/api/v1/provider-configs/{cfg['id']}/test")
check("dry_run 测试 200", r.status_code == 200)
t = r.json()
check("dry_run 不产生调用", t["called_provider"] is False)
check("dry_run 不标为真实调用", t["run_mode"] == "local_seed", t["run_mode"])

# P2/T08：真实连通测试已实现。这里只断言它**不会静默假装配**：
# 打的是不可路由的域名，必须如实报错，而不是返回一个假成功。
r = client.post(f"/api/v1/provider-configs/{cfg['id']}/test", params={"dry_run": False})
rt = r.json()
check("真实测试确实发起了调用", rt["called_provider"] is True)
check("真实测试不伪称成功", rt["status"] == "error", rt["status"])
check("真实测试不伪造用量", rt["usage"]["input_tokens"] is None)

r = client.post("/api/v1/provider-configs", json={
    "name": "缺密钥的配置", "kind": "text", "adapter_type": "openai_compatible",
    "base_url": "https://api.example.invalid/v1", "model_id": "x", "enabled": True,
})
no_key_id = r.json()["item"]["id"]
r = client.post(f"/api/v1/provider-configs/{no_key_id}/test")
check("缺密钥返回 unconfigured", r.json()["status"] == "unconfigured", r.json()["message"])
check("缺密钥时列出缺失项", "api_key" in r.json()["missing"])

r = client.post("/api/v1/provider-configs", json={
    "name": "坏 URL", "kind": "text", "adapter_type": "openai_compatible",
    "base_url": "ftp://nope", "model_id": "x",
})
check("非法 base_url 被拒 422", r.status_code == 422, str(r.status_code))

print("\n[5] T03 用量与金额语义")
r = client.get("/api/v1/usage")
check("usage 200", r.status_code == 200)
u = r.json()
check("无真实调用记录", u["real_calls_recorded"] == 0)
# P3/T14 起 /usage 改为以 provider_call 表为唯一来源，字段结构随之调整：
# 金额上限从 u["money_limit"] 移到 u["limits"]["money"]；
# 展示文案并入 u["display"]；库存并入 u["pending_review_stock"]。
# 语义没变（null ≠ 0、未知 ≠ 0），所以断言跟着结构走，不放宽标准。
check("金额上限为 null", u["limits"]["money"]["batch_money_limit_micro"] is None)
check("null 语义有解释", "不是 0 额度" in u["limits"]["money"]["meaning"],
      str(u["limits"]["money"]["meaning"]))
check("金额显示为未知而非 0", "无真实调用" in u["display"]["real_cost"],
      str(u["display"]["real_cost"]))
check("token 显示 None 语义", "不等于 0" in u["real"]["tokens_note"],
      str(u["real"]["tokens_note"]))
check("非金额约束仍生效",
      u["limits"]["non_money"]["pending_review_stock_limit"] == 3,
      str(u["limits"]["non_money"]))
check("待预览库存单列且标明是暂停不是失败",
      "暂停" in u["pending_review_stock"]["meaning"])
check("usage 标明唯一数据源是 provider_call 表",
      u["source"]["table"] == "provider_call", str(u["source"]))

r = client.get("/api/v1/usage/budget-policy")
p = r.json()
check("默认 usage_tracking", p["policy"]["money"]["cost_mode"] == "usage_tracking",
      str(p["policy"]["money"]["cost_mode"]))
check("明确 null ≠ 0", "null" in p["explanation"]["never"], p["explanation"]["never"])

r = client.post("/api/v1/usage/budget-policy/check", json={"candidate_micro": 500000})
check("usage_tracking 不因金额阻塞", r.json()["allowed"] is True, r.json()["reason"])

print("\n[6] 不变量：无自动发布入口")
paths = [getattr(r, "path", "") for r in app.routes if hasattr(r, "path")]
check("不存在 auto-publish 接口", not any("auto-publish" in str(p) for p in paths))

print(f"\n{'='*54}")
print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
print(f"临时目录：{_TMP}")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("P0 契约冒烟测试全部通过")

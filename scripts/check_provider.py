"""校验「当前已启用」的 Provider 能不能真正用起来。

不需要启动服务，也不需要在设置页里找 config_id：直接读取本机的
`storage/provider_configs.json` 与 `storage/secrets.json`，用与生产链路
同一套选择逻辑挑出真正生效的配置，然后回答「能不能用 / 为什么不能 / 怎么修」。

用法（在仓库根目录）：

    .venv/Scripts/python.exe scripts/check_provider.py                # 本地校验，不出网、不花钱
    .venv/Scripts/python.exe scripts/check_provider.py --kind text
    .venv/Scripts/python.exe scripts/check_provider.py --live         # 真实探测一次（会产生极小消耗）

`--live` 会向该接口发一条最小请求（文字模型为 "ping"），可能产生实际消耗；
不加 `--live` 时只做本地校验：地址、模型名、密钥是否齐全。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.services.provider_contract import (  # noqa: E402
    ProviderKind,
    ProviderStatus,
    ProviderStore,
    SecretStore,
)
from app.services.provider_runtime import ProviderRuntime  # noqa: E402

KIND_LABEL = {"text": "文字模型", "search": "搜索服务", "image": "图片模型"}

#: 错误码 → 用户能直接照做的排查建议（与 api/providers.py 保持一致）
ERROR_HINTS = {
    "MISSING_KEY": "没有填写 API Key，请在配置里补上密钥。",
    "AUTH": "密钥被拒绝（401/403）：检查 Key 是否正确、是否过期、是否有该模型的调用权限。",
    "RATE_LIMIT": "被限流（429）：稍后再试，或检查账号额度与并发限制。",
    "SERVER_ERROR": "服务商返回错误状态：确认 base_url 填的是接口根地址（如 https://api.xxx.com/v1），不要重复写 /chat/completions。",
    "NETWORK_TIMEOUT": "连接超时：确认该地址能从本机访问，以及是否需要代理。",
    "BAD_JSON": "返回的不是预期 JSON：base_url 很可能不是 OpenAI 兼容的 chat/completions 接口。",
    "EMPTY": "服务商返回内容为空。",
    "OUTPUT_LIMIT": "输出被截断，连通性正常但需调大输出上限。",
    "ADAPTER_NOT_IMPLEMENTED": "该适配器类型尚未实现，请换用 OpenAI 兼容接口。",
    "BAD_URL": "base_url 不是合法的 http(s) 地址。",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验当前启用中的 Provider 是否可用")
    parser.add_argument("--kind", choices=sorted(KIND_LABEL), default="text",
                        help="要校验的接口类型（默认 text）")
    parser.add_argument("--live", action="store_true",
                        help="发起一次真实最小调用（会产生极小消耗）；不加则只做本地校验")
    args = parser.parse_args(argv)

    settings = get_settings()
    store = ProviderStore(settings.storage_root / "provider_configs.json")
    secrets = SecretStore(settings.secret_store_path)
    runtime = ProviderRuntime(store, secrets)

    kind = ProviderKind(args.kind)
    label = KIND_LABEL[args.kind]
    pick = {"text": runtime.text_provider, "search": runtime.search_provider,
            "image": runtime.image_provider}[args.kind]
    cfg = pick()

    action = "真实探测（会产生极小消耗）" if args.live else "本地校验（不出网）"
    print(f"== 校验目标：当前启用的{label} ==")
    print(f"校验方式：{action}")

    if cfg is None:
        print(f"结论：不能用 —— 没有找到已启用的{label}配置，生产链路不会调用它。")
        print(f"怎么修：在「API 设置」里新增/启用一条{label}配置，"
              "填好 base_url、model_id 与 API Key 后保存。")
        return 1

    print(f"配置：{cfg.name}（id={cfg.id}）")
    print(f"      adapter={cfg.adapter_type.value}  base_url={cfg.base_url}")
    print(f"      model_id={cfg.model_id or '未填'}  超时={cfg.timeout_seconds}s")

    # 密钥的掩码信息：值本身永不打印
    if cfg.secret_ref:
        raw = secrets.get(cfg.secret_ref)
        print(f"      api_key={'已保存' if raw else '引用存在但取不到明文'}"
              f"（长度 {len(raw) if raw else 0}）")
    else:
        print(f"      api_key={'不需要' if not cfg.requires_api_key() else '未填写'}")

    result = runtime.test_connection(cfg, dry_run=not args.live)

    if not args.live:
        if result.get("status") == ProviderStatus.UNCONFIGURED.value:
            print(f"结论：配置不完整 —— {result.get('message')}")
            print(f"缺什么：{', '.join(result.get('missing') or [])}")
            return 1
        print(f"结论：本地校验通过 —— 地址、模型名与密钥都齐了（未出网、无费用）。")
        print("      这只说明配置填齐了；要确认真的能连通，请加 --live 再跑一次。")
        return 0

    status = result.get("status")
    if status == ProviderStatus.AVAILABLE.value:
        usage = result.get("usage") or {}
        print("结论：能用 —— 真实调用成功。")
        print(f"      耗时与用量：input_tokens={usage.get('input_tokens')} "
              f"output_tokens={usage.get('output_tokens')}（None 表示服务商未回传，不等于 0）")
        return 0

    print(f"结论：不能用 —— {result.get('message')}")
    if result.get("error_code"):
        print(f"      error_code={result.get('error_code')}")
        print(f"      怎么修：{ERROR_HINTS.get(result['error_code'], '查看错误信息与 error_code。')}")
    print("      注意：若错误码是 NETWORK_TIMEOUT/UNKNOWN，结果状态不明，"
          "请先去服务商后台确认这次调用的真实状态，不要直接重发。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

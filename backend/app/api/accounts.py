"""账号连接与创作相关接口的访问边界。

这里只保留**真正有意义**的约束，并把"必须是本机客户端"改成可配置的：

1. **同源**：`Origin` 必须与请求自身来源一致（挡住别的站点借用户浏览器发起的调用）。
2. **拒绝跨站**：`Sec-Fetch-Site: cross-site` 直接拒绝。
3. **写操作必须带工作台自定义头**：浏览器对跨源自定义头会先发预检，等于再加一道闸。
4. **Host 必须是本机名或私有 IP 字面量**：这条不是"麻烦"，而是防 **DNS rebinding** ——
   恶意站点把自己的域名解析到用户的内网/回环地址，就能借用户浏览器以"同源"姿态调
   本机 API。此时 Host 是攻击者的**域名**，会被这条挡下。

关于"本机"（2026-10-09 起放宽）：
- **非回环客户端**默认放行。服务能收到这种请求，本身就说明运维方已经把它绑到了可被
  访问的地址（只绑 127.0.0.1 时远程根本连不上），再按客户端 IP 拦只是把内网访问打死。
  要恢复旧的"只允许本机"，设 `CWB_ALLOW_REMOTE_ACCESS=false`。
- **回环客户端**仍要求 Host 是本机名；用反向代理时把代理域名写进 `CWB_ALLOWED_HOSTS`，
  否则 Host 是代理域名会被第 4 条拦掉。
"""
from ipaddress import ip_address
from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.config import get_settings
from ..services.platform_account import PlatformAccountService

BASE_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


def _is_loopback_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host == "testclient":  # FastAPI TestClient 的默认客户端
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private_host_literal(hostname: str) -> bool:
    """Host 是私有/回环/链路本地 IP 字面量时放行；域名一律要显式白名单。

    只放行 **IP 字面量**是刻意的：DNS rebinding 必须借一个攻击者自己的域名，
    而域名不会被这里放行。
    """
    try:
        address = ip_address(hostname)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def local_request(request: Request) -> None:
    settings = get_settings()
    hostname = (request.url.hostname or "").lower()
    if hostname not in (BASE_ALLOWED_HOSTS | settings.extra_allowed_hosts()) \
            and not _is_private_host_literal(hostname):
        raise HTTPException(403, "请从本机工作台打开账号连接。")
    if not _is_loopback_client(request) and not settings.allow_remote_access:
        raise HTTPException(403, "账号连接只允许本机访问（已通过 CWB_ALLOW_REMOTE_ACCESS=false 关闭非本机访问）。")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "请从本机工作台打开账号连接。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站访问账号连接。")
    if request.method != "GET" and request.headers.get("x-cwb-local-action") != "account-connection":
        raise HTTPException(403, "请使用工作台中的账号连接按钮。")


router = APIRouter(tags=["accounts"], dependencies=[Depends(local_request)])
service = PlatformAccountService(get_settings().storage_root)


@router.get("/accounts/douyin")
def status() -> dict:
    return service.status()


@router.post("/accounts/douyin/connect")
def connect() -> dict:
    return service.open()


@router.post("/accounts/douyin/check")
def check() -> dict:
    return service.command("check")


@router.post("/accounts/douyin/close")
def close() -> dict:
    return service.command("close")

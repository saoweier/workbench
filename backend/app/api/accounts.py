"""Account connection is restricted to the local workstation and same-origin UI."""
from ipaddress import ip_address
from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.config import get_settings
from ..services.platform_account import PlatformAccountService


def local_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        local = host == "testclient" or ip_address(host).is_loopback
    except ValueError:
        local = False
    destination = request.url.hostname
    if not local or destination not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        raise HTTPException(403, "账号连接只允许本机访问。")
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

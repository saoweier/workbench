"""发布规格 Profile 接口（T02）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.errors import NotFound
from ..services.profile_store import (
    Platform,
    PlatformLimits,
    ProfileStore,
    RenderProfile,
    engineering_default,
)

router = APIRouter(tags=["profiles"])
_settings = get_settings()
_store = ProfileStore()


class ProfileVersionIn(BaseModel):
    name: str | None = None
    render: RenderProfile | None = None
    limits: PlatformLimits | None = None
    note: str | None = None


@router.get("/profiles")
async def list_profiles(platform: Platform | None = None) -> dict:
    """列出规格版本。默认两个平台的工程默认版本都在。"""
    versions = _store.list_versions(platform)
    latest = {p: _store.latest(p).id for p in ("douyin", "xiaohongshu")}  # type: ignore[arg-type]
    return {
        "latest": latest,
        "versions": [v.model_dump(mode="json") for v in versions],
        "disclaimer": "工程默认可调值，platform_upload_verified=false，未经实际发布界面核对",
    }


@router.get("/profiles/{platform}/latest")
async def get_latest_profile(platform: Platform) -> dict:
    return _store.latest(platform).model_dump(mode="json")


@router.get("/profiles/{platform}/default")
async def get_engineering_default(platform: Platform) -> dict:
    """返回出厂默认值，便于前端"恢复默认"。"""
    return engineering_default(platform).model_dump(mode="json")


@router.post("/profiles/{platform}/versions", status_code=201)
async def create_profile_version(platform: Platform, payload: ProfileVersionIn) -> dict:
    """产生新版本，旧版本标记 superseded_by，内容不被修改。"""
    new = _store.new_version(
        platform,
        render=payload.render,
        limits=payload.limits,
        name=payload.name,
        note=payload.note,
    )
    return new.model_dump(mode="json")


@router.get("/profiles/versions/{version_id}")
async def get_profile_version(version_id: str) -> dict:
    v = _store.get(version_id)
    if not v:
        raise NotFound(f"规格版本不存在: {version_id}")
    return v.model_dump(mode="json")


@router.post("/profiles/{platform}/verify")
async def mark_verified(platform: Platform, confirmed: bool = False) -> dict:
    """标记上传兼容性。P0 阶段即使调用也只记录待集成，不伪造已验证。"""
    latest = _store.latest(platform)
    if not confirmed:
        return {
            "platform": platform,
            "platform_upload_verified": False,
            "integration_status": "integration_pending",
            "message": "未确认，仍为待集成状态",
        }
    return {
        "platform": platform,
        "platform_upload_verified": latest.platform_upload_verified,
        "integration_status": "integration_pending",
        "message": "P0 阶段不落库验证结论；需在 P1 用真实账号上传结果登记",
    }

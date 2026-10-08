"""发布规格 Profile（T02）。

核心原则：
- 尺寸 / 页数 / 格式 / 文字上限**全部可编辑**，不是写死的"平台规定"。
- 工程默认值 platform_upload_verified=False，含义是"仅为便于开发的排版值，
  不是平台官方规格，也没有经过实际上传兼容性验证"。
- 每次修改产生新版本，历史版本不可变；批次固定引用某一版本。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Platform = Literal["douyin", "xiaohongshu"]
ImageFormat = Literal["png", "jpeg", "webp"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformLimits(BaseModel):
    """平台侧限制。全部可调整。"""

    max_title_chars: int = Field(default=20, ge=1, le=200)
    max_caption_chars: int = Field(default=1000, ge=1, le=20000)
    min_pages: int = Field(default=1, ge=1, le=100)
    max_pages: int = Field(default=18, ge=1, le=100)
    max_heading_chars: int = Field(default=24, ge=1, le=200)
    max_body_chars_per_page: int = Field(default=120, ge=1, le=2000)
    max_body_lines_per_page: int = Field(default=6, ge=1, le=30)

    @field_validator("max_pages")
    @classmethod
    def _page_range_ok(cls, v: int, info) -> int:
        mn = info.data.get("min_pages", 1)
        if v < mn:
            raise ValueError(f"max_pages({v}) 不能小于 min_pages({mn})")
        return v


class RenderProfile(BaseModel):
    """渲染规格。工程默认可调值。"""

    width_px: int = Field(default=1080, ge=320, le=4096)
    height_px: int = Field(default=1440, ge=320, le=4096)
    format: ImageFormat = "png"
    dpi: int = Field(default=96, ge=36, le=600)
    safe_margin_px: int = Field(default=72, ge=0, le=400)
    font_family: str = "Noto Sans CJK SC"
    template_versions: list[str] = Field(default_factory=lambda: ["cover@1", "checklist@1"])

    @property
    def aspect_ratio(self) -> str:
        from math import gcd

        g = gcd(self.width_px, self.height_px) or 1
        return f"{self.width_px // g}:{self.height_px // g}"


class ProfileVersion(BaseModel):
    """某个平台的版本化规格。创建后不可变。"""

    id: str
    platform: Platform
    version: int
    name: str
    render: RenderProfile
    limits: PlatformLimits
    platform_upload_verified: bool = False
    verification_note: str = (
        "工程默认排版值，未在实际发布界面核对，不代表平台官方规格或上传兼容性结论"
    )
    source: Literal["engineering_default", "user_confirmed", "platform_verified"] = (
        "engineering_default"
    )
    created_at: datetime = Field(default_factory=_now)
    superseded_by: str | None = None


# ---------------------------------------------------------------- 工程默认值

def engineering_default(platform: Platform) -> ProfileVersion:
    """返回某平台的工程默认规格。抖音首图为 3:4，小红书 3:4，均为开发用可调值。"""
    if platform == "douyin":
        render = RenderProfile(width_px=1080, height_px=1440, format="png")
        limits = PlatformLimits(
            max_title_chars=20,
            max_caption_chars=1000,
            max_pages=18,
            max_heading_chars=24,
            max_body_chars_per_page=120,
            max_body_lines_per_page=6,
        )
    else:  # xiaohongshu
        render = RenderProfile(width_px=1080, height_px=1440, format="png")
        limits = PlatformLimits(
            max_title_chars=20,
            max_caption_chars=1000,
            max_pages=18,
            max_heading_chars=24,
            max_body_chars_per_page=120,
            max_body_lines_per_page=6,
        )

    return ProfileVersion(
        id=f"pp-default-{platform}-v1",
        platform=platform,
        version=1,
        name="editorial-development-default",
        render=render,
        limits=limits,
        platform_upload_verified=False,
        source="engineering_default",
    )


class ProfileStore:
    """内存 + 文件双写的规格版本库（P0 用文件持久化，P1 迁到 DB）。"""

    def __init__(self, path=None) -> None:
        from ..core.config import get_settings

        self._path = path or (get_settings().storage_root / "profiles.json")
        self._versions: dict[str, ProfileVersion] = {}
        self._latest: dict[Platform, str] = {}
        self._load()

    def _load(self) -> None:
        import json

        if not self._path.exists():
            for p in ("douyin", "xiaohongshu"):
                pv = engineering_default(p)  # type: ignore[arg-type]
                self._versions[pv.id] = pv
                self._latest[pv.platform] = pv.id
            self._save()
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._versions = {}
        for item in raw.get("versions", []):
            pv = ProfileVersion.model_validate(item)
            self._versions[pv.id] = pv
        self._latest = raw.get("latest", {})

    def _save(self) -> None:
        import json

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "latest": self._latest,
            "versions": [v.model_dump(mode="json") for v in self._versions.values()],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def latest(self, platform: Platform) -> ProfileVersion:
        self._load()
        return self._versions[self._latest[platform]]

    def get(self, version_id: str) -> ProfileVersion | None:
        self._load()
        return self._versions.get(version_id)

    def list_versions(self, platform: Platform | None = None) -> list[ProfileVersion]:
        self._load()
        vals = list(self._versions.values())
        if platform:
            vals = [v for v in vals if v.platform == platform]
        return sorted(vals, key=lambda v: (v.platform, v.version))

    def new_version(
        self,
        platform: Platform,
        *,
        render: RenderProfile | None = None,
        limits: PlatformLimits | None = None,
        name: str | None = None,
        note: str | None = None,
    ) -> ProfileVersion:
        """产生新版本；旧版本标记 superseded_by，但不修改其内容。"""
        import uuid

        old = self.latest(platform)
        new = ProfileVersion(
            id=f"pp-{platform}-{uuid.uuid4().hex[:10]}",
            platform=platform,
            version=old.version + 1,
            name=name or old.name,
            render=render or old.render,
            limits=limits or old.limits,
            platform_upload_verified=False,
            verification_note=note or old.verification_note,
            source="engineering_default",
        )
        old.superseded_by = new.id
        self._versions[new.id] = new
        self._latest[platform] = new.id
        self._save()
        return new

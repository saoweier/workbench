"""C001 seed 校验接口（T01）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ..core.config import get_settings
from ..core.errors import NotFound
from ..services.seed_validator import validate_seed

router = APIRouter(tags=["seeds"])
_settings = get_settings()


@router.get("/seeds")
async def list_seeds() -> dict:
    """列出 examples/ 下可用的 seed 输入。"""
    root = _settings.examples_dir
    found = []
    if root.exists():
        for p in sorted(root.rglob("seed.json")):
            found.append({
                "path": str(p.relative_to(root.parent)),
                "display_id": p.parent.name,
                "bytes": p.stat().st_size,
            })
    return {"examples_dir": str(root), "seeds": found}


@router.post("/seeds/{display_id}/validate")
async def validate(display_id: str) -> dict:
    """校验指定 C001 类 seed：JSON / 页序 / 引用 / 来源存在性。"""
    root = _settings.examples_dir
    candidate = root / display_id / "seeds" / display_id / "seed.json"
    if not candidate.exists():
        matches = list(root.rglob("seed.json")) if root.exists() else []
        candidate = next((m for m in matches if m.parent.name == display_id), None)  # type: ignore[assignment]
    if not candidate or not Path(candidate).exists():
        raise NotFound(f"未找到 {display_id} 的 seed.json")

    report = validate_seed(candidate)
    return {
        "passed": report.passed,
        "summary": report.summary(),
        "report": report.model_dump(mode="json"),
    }

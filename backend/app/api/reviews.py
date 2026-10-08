"""复盘接口（P4/T19）。

对应 `docs/03-data-and-api.md` §5 的 `POST /contents/{id}/reviews`，
以及不变量第 7 条（复盘按固定输入快照生成）。

## 为什么接口要显式返回 `limitations`

这个接口最大的风险不是"算错"，而是**被人当成结论用**。
"这条播放 3 万"是事实；"所以封面要这样做"是故事。
接口把 `observations` / `hypotheses` / `experiments` 分成三栏返回，
再把 `limitations` 和 `data_sufficiency` 放在同一层——
让人一眼能看到"这份报告能支持什么结论、不能支持什么结论"。

**没有数据时这个接口不会返回空数组让你自己体会**，
它会明确说"没有发布记录，不能输出任何传播效果结论"。

路由一览：
    GET  /contents/{id}/reviews      某内容的历史复盘（版本列表）
    POST /contents/{id}/reviews      生成一份新复盘（绑定当前快照）
    GET  /reviews/{id}               单份复盘详情
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import get_settings
from ..models.entities import Base, enable_sqlite_fk
from ..services.review_service import SUFFICIENCY_LEVELS, ReviewService

router = APIRouter(tags=["reviews"])
_settings = get_settings()

_engine = create_engine(_settings.database_url, future=True)
enable_sqlite_fk(_engine)
Base.metadata.create_all(_engine)
SessionFactory = sessionmaker(bind=_engine, future=True)

_svc = ReviewService(SessionFactory, _settings)


@router.get("/contents/{content_id}/reviews")
async def list_reviews(content_id: str) -> dict:
    """该内容的历史复盘。**同一内容可以有多版**，新数据不会改旧结论。"""
    return _svc.list_reports(content_id=content_id)


@router.post("/contents/{content_id}/reviews")
async def create_review(content_id: str, payload: dict = Body(default={})) -> dict:
    """生成复盘。

    请求体（都可选）：
    ```json
    {
      "hypotheses": [{
        "statement": "封面信息量更大可能提升了点击率",
        "alternative_explanations": ["发布时间处于流量高峰", "本期账号推荐权重整体上升"],
        "confidence": "low"
      }],
      "experiments": [{"proposal": "只改封面，选题与时间不变", "single_variable": "cover"}],
      "next_topics": ["选题 A", "选题 B"]
    }
    ```

    **`hypotheses` 里每条都必须带 `alternative_explanations`。**
    只给一个解释不给替代解释会被 422 拒绝——
    给不出竞争解释，说明手里是直觉不是假设。
    """
    try:
        data = _svc.generate(
            content_id,
            created_by="user",
            run_mode=payload.get("run_mode", "real"),
            hypotheses=payload.get("hypotheses"),
            experiments=payload.get("experiments"),
            next_topics=payload.get("next_topics"),
        )
    except ValueError as e:
        msg = str(e)
        code = "HYPOTHESIS_NEEDS_ALTERNATIVE" if "alternative_explanations" in msg else "BAD_REQUEST"
        status = 422 if code == "HYPOTHESIS_NEEDS_ALTERNATIVE" else 400
        if "内容不存在" in msg:
            status, code = 404, "CONTENT_NOT_FOUND"
        raise HTTPException(status_code=status, detail={"code": code, "message": msg})

    data["capabilities"] = {
        "auto_conclude": False,
        "note": (
            "本接口只呈现事实与可能解释，不产出「因为 A 所以 B」的因果结论。"
            "hypotheses 必须附替代解释；experiments 建议只改一个变量。"
        ),
        "sufficiency_levels": list(SUFFICIENCY_LEVELS),
    }
    return data


@router.get("/reviews/{report_id}")
async def get_review(report_id: str) -> dict:
    try:
        return _svc.get_report(report_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={
            "code": "REPORT_NOT_FOUND", "message": str(e),
        })

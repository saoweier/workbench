"""统一错误码。与 03-data-and-api.md 第 6 节一致。"""
from __future__ import annotations

import uuid


class AppError(Exception):
    status_code = 400
    code = "APP_ERROR"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class StateConflict(AppError):
    """状态冲突，如过期批准覆盖新图/新文案。"""

    status_code = 409
    code = "STATE_CONFLICT"


class BudgetLimitError(AppError):
    """预算不足。仅在启用硬上限（hard_cap）时使用。"""

    status_code = 409
    code = "BUDGET_LIMIT"


class IdempotencyConflict(AppError):
    status_code = 409
    code = "IDEMPOTENCY_KEY_REUSED"


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class NotConfigured(AppError):
    """Provider 或搜索未配置。不是错误状态，是明确的可继续状态。"""

    status_code = 409
    code = "PROVIDER_NOT_CONFIGURED"


def new_id() -> str:
    return str(uuid.uuid4())


class TaskStopped(StateConflict):
    def __init__(self, state: str):
        self.state = state
        super().__init__("任务已暂停" if state == "paused" else "任务已取消")

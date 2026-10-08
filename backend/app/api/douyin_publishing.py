from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .accounts import local_request
from ..core.config import get_settings
from ..services.douyin_publishing import DouyinPublishingService

router = APIRouter(tags=["douyin-publishing"], dependencies=[Depends(local_request)])
service = DouyinPublishingService(get_settings())


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform_revision_id: str = Field(min_length=1, max_length=36)


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Confirmation(Version):
    account_id: str = Field(min_length=1, max_length=64)
    confirmed: bool = Field(strict=True)


@router.get("/douyin/tasks")
def tasks():
    return service.list()


@router.get("/douyin/tasks/{task_id}")
def task(task_id: str):
    return service.get(task_id)


@router.post("/douyin/tasks")
def plan(payload: Plan):
    return service.plan(payload.platform_revision_id)


@router.post("/douyin/tasks/{task_id}/upload")
def upload(task_id: str, payload: Version):
    return service.upload(task_id, payload.expected_payload_hash)


@router.post("/douyin/tasks/{task_id}/confirm")
def confirm(task_id: str, payload: Confirmation):
    return service.confirm(task_id, payload.expected_payload_hash, payload.account_id, payload.confirmed)


@router.post("/douyin/tasks/{task_id}/observe")
def observe(task_id: str):
    return service.request_observation(task_id)


@router.post("/douyin/tasks/{task_id}/cancel")
def cancel(task_id: str):
    return service.cancel(task_id)

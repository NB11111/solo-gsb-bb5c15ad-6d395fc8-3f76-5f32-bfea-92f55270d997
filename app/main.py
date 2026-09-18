"""封签链校验 API 入口。"""
from __future__ import annotations

from fastapi import FastAPI

from .batch import validate_batch
from .models import (
    BatchValidateRequest,
    BatchValidateResponse,
    ValidateRequest,
    ValidateResponse,
)
from .validator import validate_events

app = FastAPI(
    title="封签链校验 API",
    description="用于展品运输交接场景的封签链状态机校验服务",
    version="1.1.0",
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """健康检查。"""
    return {"status": "ok"}


@app.post(
    "/api/v1/seal-chain/validate",
    response_model=ValidateResponse,
    tags=["seal-chain"],
    summary="校验封签链事件序列",
)
def validate(request: ValidateRequest) -> ValidateResponse:
    """按状态机逐事件校验，返回最终保管人、当前封签、逐事件状态与全部违规。"""
    return validate_events(request.events)


@app.post(
    "/api/v1/seal-chain/batch-validate",
    response_model=BatchValidateResponse,
    tags=["seal-chain"],
    summary="批量校验多条封签链并检查跨链封签占用冲突",
)
def batch_validate(request: BatchValidateRequest) -> BatchValidateResponse:
    """各链先独立按状态机校验，再按输入顺序建立全批次封签占用表。

    同一 seal_id 被不同链的有效 seal/reseal 事件占用时，相关事件均标记
    cross_chain_conflict；冲突不改变各链状态，但批次 valid 为 false。
    """
    return validate_batch(request)

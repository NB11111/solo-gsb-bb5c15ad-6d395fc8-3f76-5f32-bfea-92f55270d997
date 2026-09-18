"""请求 / 响应数据模型。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """封签链支持的四种事件类型。"""

    SEAL = "seal"          # 封装：首次（或开封后）以封签封装展品
    HANDOVER = "handover"  # 交接：保管人变更
    UNSEAL = "unseal"      # 开封：拆除当前封签
    RESEAL = "reseal"      # 重封：开封后使用新封签重新封装


class Event(BaseModel):
    """单个封签链事件。timestamp 使用 ISO 8601 格式。"""

    type: EventType
    timestamp: datetime
    seal_id: Optional[str] = Field(default=None, description="封签编号，seal/reseal 事件必填")
    custodian: Optional[str] = Field(default=None, description="保管人，seal/reseal/handover 事件必填")


class ValidateRequest(BaseModel):
    """校验请求：按时间排列的事件列表。"""

    events: List[Event] = Field(default_factory=list)


class EventResult(BaseModel):
    """逐事件判定结果。"""

    index: int
    type: EventType
    status: str = Field(description="ok 或 violation")
    reason: Optional[str] = Field(default=None, description="违规原因，status 为 violation 时给出")
    conflict: Optional[str] = Field(
        default=None,
        description="跨链封签占用冲突标记（cross_chain_conflict），仅批量校验时可能出现",
    )


class Violation(BaseModel):
    """违规事件的位置与原因。"""

    index: int
    reason: str


class ValidateResponse(BaseModel):
    """校验响应。"""

    valid: bool = Field(description="整条链是否无任何违规")
    sealed: bool = Field(description="处理完所有有效事件后是否处于封装状态")
    final_custodian: Optional[str] = Field(description="最终保管人")
    current_seal: Optional[str] = Field(description="当前有效封签编号，未封装时为 null")
    events: List[EventResult] = Field(description="逐事件状态")
    violations: List[Violation] = Field(description="所有违规事件的位置与原因")


class ChainInput(BaseModel):
    """批量校验中的单条事件链。"""

    chain_id: str = Field(description="事件链标识，用于定位冲突与汇总排序")
    events: List[Event] = Field(default_factory=list)


class BatchValidateRequest(BaseModel):
    """批量校验请求：多条带 chain_id 的事件链。"""

    chains: List[ChainInput] = Field(default_factory=list)


class SealConflict(BaseModel):
    """跨链封签占用冲突项。"""

    seal_id: str = Field(description="被重复占用的封签编号")
    chain_id: str = Field(description="本冲突事件所在的链")
    event_index: int = Field(description="本冲突事件在链内的下标（从 0 开始）")
    first_chain_id: str = Field(description="批次内首次占用该封签的链")


class ChainResult(ValidateResponse):
    """单链校验结果（与单链校验一致）及其涉及的跨链冲突。"""

    chain_id: str
    conflicts: List[SealConflict] = Field(
        default_factory=list, description="该链涉及的跨链封签占用冲突"
    )


class ChainSummary(BaseModel):
    """单链汇总项。"""

    chain_id: str
    valid: bool
    sealed: bool
    final_custodian: Optional[str]
    current_seal: Optional[str]
    violation_count: int = Field(description="链内违规事件数")
    conflict_count: int = Field(description="该链涉及的跨链冲突事件数")


class BatchValidateResponse(BaseModel):
    """批量校验响应。"""

    valid: bool = Field(description="批次整体是否通过：所有链无违规且无跨链封签冲突")
    chains: List[ChainResult] = Field(description="按输入顺序的各链校验结果")
    conflicts: List[SealConflict] = Field(description="全批次跨链封签冲突，按检出顺序排列")
    summary: List[ChainSummary] = Field(description="按 chain_id 稳定排序的各链汇总")

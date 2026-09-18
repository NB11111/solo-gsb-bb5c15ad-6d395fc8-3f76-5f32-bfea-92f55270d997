"""批量封签占用检查。

在单链状态机校验（validator.validate_events）之上，按输入顺序建立全批次
封签占用表：同一 seal_id 被不同链的“有效”（status=ok）seal/reseal 事件
占用时，两条链的对应事件均标记 cross_chain_conflict。

冲突不回改各链的校验状态（valid / violations / 保管人等保持不变），
但批次的 valid 必须为 false。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .models import (
    BatchValidateRequest,
    BatchValidateResponse,
    ChainResult,
    ChainSummary,
    EventType,
    SealConflict,
)
from .validator import validate_events

CROSS_CHAIN_CONFLICT = "cross_chain_conflict"


def validate_batch(request: BatchValidateRequest) -> BatchValidateResponse:
    """逐链独立校验后执行全批次封签占用检查，返回批量校验结果。"""
    # 第一步：各链按现有状态机规则独立校验，结果不因冲突回改
    chains: List[ChainResult] = []
    for chain in request.chains:
        result = validate_events(chain.events)
        chains.append(
            ChainResult(
                chain_id=chain.chain_id,
                valid=result.valid,
                sealed=result.sealed,
                final_custodian=result.final_custodian,
                current_seal=result.current_seal,
                events=result.events,
                violations=result.violations,
                conflicts=[],
            )
        )

    # 第二步：按输入顺序建立全批次封签占用表（仅统计各链内有效的 seal/reseal 事件）
    occupancy: Dict[str, Tuple[int, int]] = {}  # seal_id -> (链下标, 事件下标)
    conflicts: List[SealConflict] = []

    for chain_pos, (chain_input, chain_result) in enumerate(zip(request.chains, chains)):
        for event_input, event_result in zip(chain_input.events, chain_result.events):
            if event_result.status != "ok":
                continue  # 违规事件不参与占用
            if event_input.type not in (EventType.SEAL, EventType.RESEAL):
                continue
            seal_id = event_input.seal_id
            assert seal_id is not None  # 有效 seal/reseal 事件必然携带 seal_id

            first = occupancy.get(seal_id)
            if first is None:
                occupancy[seal_id] = (chain_pos, event_result.index)
                continue

            first_chain_pos, first_event_index = first
            first_chain_id = chains[first_chain_pos].chain_id
            if first_chain_id == chain_result.chain_id:
                continue  # 同一 chain_id 的复用已由单链规则拦截，不计跨链冲突

            # 首次占用事件与当前事件均标记 cross_chain_conflict（首次事件只标记一次）
            first_event_result = chains[first_chain_pos].events[first_event_index]
            if first_event_result.conflict is None:
                first_event_result.conflict = CROSS_CHAIN_CONFLICT
                first_conflict = SealConflict(
                    seal_id=seal_id,
                    chain_id=first_chain_id,
                    event_index=first_event_index,
                    first_chain_id=first_chain_id,
                )
                chains[first_chain_pos].conflicts.append(first_conflict)
                conflicts.append(first_conflict)

            event_result.conflict = CROSS_CHAIN_CONFLICT
            conflict = SealConflict(
                seal_id=seal_id,
                chain_id=chain_result.chain_id,
                event_index=event_result.index,
                first_chain_id=first_chain_id,
            )
            chain_result.conflicts.append(conflict)
            conflicts.append(conflict)

    valid = all(chain.valid for chain in chains) and not conflicts
    # 按 chain_id 稳定排序（sorted 稳定性保证相同 chain_id 保持输入顺序）
    summary = sorted(
        (
            ChainSummary(
                chain_id=chain.chain_id,
                valid=chain.valid,
                sealed=chain.sealed,
                final_custodian=chain.final_custodian,
                current_seal=chain.current_seal,
                violation_count=len(chain.violations),
                conflict_count=len(chain.conflicts),
            )
            for chain in chains
        ),
        key=lambda item: item.chain_id,
    )
    return BatchValidateResponse(valid=valid, chains=chains, conflicts=conflicts, summary=summary)

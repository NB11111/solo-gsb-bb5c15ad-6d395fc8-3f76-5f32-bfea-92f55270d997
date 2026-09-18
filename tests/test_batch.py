"""批量封签占用检查（batch-validate）单元测试与 API 集成测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.batch import CROSS_CHAIN_CONFLICT, validate_batch
from app.main import app
from app.models import BatchValidateRequest, ChainInput, Event, EventType

client = TestClient(app)

T = "2026-09-18T%02d:00:00Z"


def ev(i: int, type_: EventType, **kw) -> Event:
    return Event(type=type_, timestamp=T % i, **kw)


def chain(chain_id: str, events) -> ChainInput:
    return ChainInput(chain_id=chain_id, events=events)


def test_batch_no_conflict_valid():
    req = BatchValidateRequest(
        chains=[
            chain("B", [ev(1, EventType.SEAL, seal_id="S201", custodian="Bob")]),
            chain("A", [
                ev(1, EventType.SEAL, seal_id="S101", custodian="Alice"),
                ev(2, EventType.HANDOVER, custodian="Carol"),
            ]),
        ]
    )
    resp = validate_batch(req)
    assert resp.valid is True
    assert resp.conflicts == []
    assert [c.chain_id for c in resp.chains] == ["B", "A"]  # 各链结果保持输入顺序
    assert all(c.valid for c in resp.chains)
    # 汇总按 chain_id 稳定排序
    assert [s.chain_id for s in resp.summary] == ["A", "B"]
    assert resp.summary[0].final_custodian == "Carol"
    assert all(s.conflict_count == 0 for s in resp.summary)


def test_cross_chain_conflict_marks_both_chains():
    req = BatchValidateRequest(
        chains=[
            chain("A", [ev(1, EventType.SEAL, seal_id="S001", custodian="Alice")]),
            chain("B", [
                ev(1, EventType.SEAL, seal_id="S901", custodian="Bob"),
                ev(2, EventType.UNSEAL),
                ev(3, EventType.RESEAL, seal_id="S001", custodian="Bob"),  # 占用 A 的封签
            ]),
        ]
    )
    resp = validate_batch(req)
    # 冲突不改变各链状态
    assert all(c.valid for c in resp.chains)
    assert resp.chains[0].current_seal == "S001"
    assert resp.chains[1].current_seal == "S001"
    # 但批次 valid 必须为 false
    assert resp.valid is False
    # 两条链的对应事件均标记 cross_chain_conflict
    assert resp.chains[0].events[0].conflict == CROSS_CHAIN_CONFLICT
    assert resp.chains[0].events[0].status == "ok"
    assert resp.chains[1].events[2].conflict == CROSS_CHAIN_CONFLICT
    assert resp.chains[1].events[0].conflict is None
    # 冲突项包含 seal_id、chain_id、event_index、首次占用链
    assert [c.model_dump() for c in resp.conflicts] == [
        {"seal_id": "S001", "chain_id": "A", "event_index": 0, "first_chain_id": "A"},
        {"seal_id": "S001", "chain_id": "B", "event_index": 2, "first_chain_id": "A"},
    ]
    assert [len(c.conflicts) for c in resp.chains] == [1, 1]
    summary = {s.chain_id: s for s in resp.summary}
    assert summary["A"].conflict_count == 1
    assert summary["B"].conflict_count == 1


def test_violating_event_does_not_occupy_seal():
    # 链 B 对 S001 的使用本身是违规事件（已封装状态下重复封装），不构成占用
    req = BatchValidateRequest(
        chains=[
            chain("A", [ev(1, EventType.SEAL, seal_id="S001", custodian="Alice")]),
            chain("B", [
                ev(1, EventType.SEAL, seal_id="S001", custodian="Bob"),  # 有效，占用 S001
                ev(2, EventType.SEAL, seal_id="S001", custodian="Bob"),  # 违规，不参与占用
            ]),
        ]
    )
    resp = validate_batch(req)
    assert resp.valid is False
    assert resp.chains[1].valid is False  # 链 B 自身有违规
    assert [v.index for v in resp.chains[1].violations] == [1]
    # 仅 A(0) 与 B(0) 两个有效事件冲突，违规的 B(1) 不产生冲突项
    assert [(c.chain_id, c.event_index) for c in resp.conflicts] == [("A", 0), ("B", 0)]
    assert resp.chains[1].events[1].conflict is None


def test_first_occupier_marked_once_with_three_chains():
    req = BatchValidateRequest(
        chains=[
            chain("A", [ev(1, EventType.SEAL, seal_id="S001", custodian="Alice")]),
            chain("B", [ev(1, EventType.SEAL, seal_id="S001", custodian="Bob")]),
            chain("C", [ev(1, EventType.SEAL, seal_id="S001", custodian="Carol")]),
        ]
    )
    resp = validate_batch(req)
    assert resp.valid is False
    # 首次占用事件只产生一条冲突项，三条链各一条，共三条
    assert [(c.chain_id, c.event_index, c.first_chain_id) for c in resp.conflicts] == [
        ("A", 0, "A"),
        ("B", 0, "A"),
        ("C", 0, "A"),
    ]
    assert [len(c.conflicts) for c in resp.chains] == [1, 1, 1]


def test_same_chain_id_reuse_is_not_cross_chain_conflict():
    # 相同 chain_id 视为同一链：其封签复用由单链规则处理，不计跨链冲突
    req = BatchValidateRequest(
        chains=[
            chain("A", [ev(1, EventType.SEAL, seal_id="S001", custodian="Alice")]),
            chain("A", [ev(1, EventType.SEAL, seal_id="S001", custodian="Alice")]),
        ]
    )
    resp = validate_batch(req)
    assert resp.valid is True
    assert resp.conflicts == []
    # 稳定排序：相同 chain_id 保持输入顺序
    assert [s.chain_id for s in resp.summary] == ["A", "A"]


def test_chain_violation_alone_fails_batch():
    req = BatchValidateRequest(
        chains=[
            chain("A", [ev(1, EventType.HANDOVER, custodian="Bob")]),  # 未封装交接，违规
            chain("B", [ev(1, EventType.SEAL, seal_id="S001", custodian="Bob")]),
        ]
    )
    resp = validate_batch(req)
    assert resp.valid is False
    assert resp.conflicts == []
    assert [c.valid for c in resp.chains] == [False, True]
    summary = {s.chain_id: s for s in resp.summary}
    assert summary["A"].violation_count == 1
    assert summary["B"].violation_count == 0


def test_empty_batch_is_valid():
    resp = validate_batch(BatchValidateRequest(chains=[]))
    assert resp.valid is True
    assert resp.chains == []
    assert resp.conflicts == []
    assert resp.summary == []


def test_api_batch_validate_endpoint():
    payload = {
        "chains": [
            {"chain_id": "Z", "events": [
                {"type": "seal", "timestamp": "2026-09-18T10:00:00Z",
                 "seal_id": "S001", "custodian": "Alice"},
            ]},
            {"chain_id": "Y", "events": [
                {"type": "seal", "timestamp": "2026-09-18T10:00:00Z",
                 "seal_id": "S001", "custodian": "Bob"},
                {"type": "handover", "timestamp": "2026-09-18T11:00:00Z",
                 "custodian": "Carol"},
            ]},
        ]
    }
    r = client.post("/api/v1/seal-chain/batch-validate", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert [c["chain_id"] for c in body["chains"]] == ["Z", "Y"]
    assert body["chains"][0]["events"][0]["conflict"] == CROSS_CHAIN_CONFLICT
    assert body["chains"][1]["events"][0]["conflict"] == CROSS_CHAIN_CONFLICT
    assert body["chains"][1]["events"][1]["conflict"] is None
    assert body["conflicts"] == [
        {"seal_id": "S001", "chain_id": "Z", "event_index": 0, "first_chain_id": "Z"},
        {"seal_id": "S001", "chain_id": "Y", "event_index": 0, "first_chain_id": "Z"},
    ]
    assert [s["chain_id"] for s in body["summary"]] == ["Y", "Z"]
    assert body["summary"][0]["final_custodian"] == "Carol"


def test_api_batch_validate_requires_chain_id():
    payload = {"chains": [{"events": []}]}
    assert client.post("/api/v1/seal-chain/batch-validate", json=payload).status_code == 422

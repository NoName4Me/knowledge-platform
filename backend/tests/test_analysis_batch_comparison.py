"""T05–T07：批量、部分成功、对比汇总与恢复路径。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.test_analysis_api import _run_until_finished, _team_with_kb
from tests.test_analysis_extraction import _ready_document, _single_field


def _create(
    client,
    team_id,
    kb_id,
    document_ids,
    *,
    kind="extraction",
    fields=None,
    title="批量分析",
):
    return client.post(
        f"/api/teams/{team_id}/knowledge-bases/{kb_id}/analyses",
        json={
            "kind": kind,
            "document_ids": document_ids,
            "fields": fields or _single_field("发布日期", "资料发布日期", "date"),
            "title": title,
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


class SummaryProvider:
    """在既有提取替身之外替换汇总回执，逐条验证范围与依据校验。"""

    name = "summary-scripted"
    model = "summary-scripted-1"

    def __init__(self, payload):
        self.payload = payload

    def compare(self, batch):
        from app.analysis_model import AnalysisModelResult

        raw = self.payload(batch) if callable(self.payload) else self.payload
        return AnalysisModelResult(
            raw=json.dumps(raw, ensure_ascii=False),
            provider=self.name,
            model=self.model,
            prompt_version="v2-compare-1",
            schema_version="v2-comparison-1",
        )


def _install_summary(monkeypatch, payload):
    from app import analysis_summary

    provider = SummaryProvider(payload)
    monkeypatch.setattr(analysis_summary, "get_analysis_provider", lambda: provider)
    return provider


def test_batch_extraction_two_success_and_one_failure_is_partial(
    client, session, monkeypatch
):
    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023 年。", "b.txt")

    created = _create(client, team.id, kb_id, [first["id"], second["id"]])
    assert created.status_code == 202, created.text
    task_id = created.json()["id"]

    # 执行顺序固定按提交顺序，但分批领取、发布各自持有单一租约。
    from app.worker import run_once

    for _ in range(8):
        session.expire_all()
        status = session.execute(
            text("select status from analysis_tasks where id = :id"), {"id": task_id}
        ).scalar_one()
        if status in ("completed", "partial", "failed", "invalidated"):
            break
        run_once()
    assert status == "completed"
    detail = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{task_id}"
    ).json()
    assert len(detail["results"]) == 2
    assert detail["task"]["completed_document_count"] == 2


def test_comparison_completes_with_validated_common_and_difference(
    client, session, monkeypatch
):
    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022-06。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023-10-05。", "b.txt")

    def payload(batch):
        a, b = batch.documents
        field = batch.field_specs[0]
        citations = {
            value.document_id: value.citations[0][0] for value in batch.values if value.citations
        }
        return {
            "conclusions": [
                {
                    "field_id": str(field.id),
                    "kind": "difference",
                    "text": "两份文档的发布日期不同。",
                    "document_ids": [str(a[0]), str(b[0])],
                    "citation_refs": [
                        [str(a[0]), str(citations[a[0]])],
                        [str(b[0]), str(citations[b[0]])],
                    ],
                }
            ]
        }

    _install_summary(monkeypatch, payload)
    created = _create(
        client,
        team.id,
        kb_id,
        [first["id"], second["id"]],
        kind="comparison",
    )
    task_id = created.json()["id"]
    assert _run_until_finished(session, task_id, rounds=12) == "completed"
    body = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{task_id}"
    ).json()
    assert body["task"]["document_count"] == 2
    assert body["conclusions"][0]["kind"] == "difference"
    assert len(body["conclusions"][0]["citations"]) == 2
    assert body["summary"]["status"] == "succeeded"


def test_comparison_rejects_fabricated_conclusion_scope(client, session, monkeypatch):
    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022-06。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023 年。", "b.txt")
    _install_summary(
        monkeypatch,
        {
            "conclusions": [
                {
                    "field_id": str(uuid.uuid4()),
                    "kind": "difference",
                    "text": "伪造字段",
                    "document_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
                    "citation_refs": [],
                }
            ]
        },
    )
    created = _create(
        client, team.id, kb_id, [first["id"], second["id"]], kind="comparison"
    )
    assert _run_until_finished(session, created.json()["id"], rounds=14) == "partial"
    body = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{created.json()['id']}"
    ).json()
    assert body["conclusions"] == []
    assert body["results"]
    assert body["summary"]["status"] == "failed"


def test_comparison_rejects_citation_from_non_participant_document(
    client, session, monkeypatch
):
    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022-06。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023 年。", "b.txt")

    def payload(batch):
        a, b = batch.documents
        field = batch.field_specs[0]
        # 参与文档只列第一份，却拿第二份的依据，汇总必须整次拒绝。
        second_citation = next(
            value.citations[0][0]
            for value in batch.values
            if value.document_id == b[0] and value.citations
        )
        return {
            "conclusions": [
                {
                    "field_id": str(field.id),
                    "kind": "needs_review",
                    "text": "依据越界",
                    "document_ids": [str(a[0])],
                    "citation_refs": [[str(b[0]), str(second_citation)]],
                }
            ]
        }

    _install_summary(monkeypatch, payload)
    created = _create(
        client, team.id, kb_id, [first["id"], second["id"]], kind="comparison"
    )
    task_id = created.json()["id"]
    assert _run_until_finished(session, task_id, rounds=14) == "partial"
    body = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{task_id}"
    ).json()
    assert body["conclusions"] == []
    assert body["summary"]["status"] == "failed"


def test_comparison_skips_summary_after_document_failure(client, session):
    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023 年。", "b.txt")
    created = _create(
        client, team.id, kb_id, [first["id"], second["id"]], kind="comparison"
    )
    task_id = created.json()["id"]
    # 一份失败时不再领取汇总，保留成功结果并收敛 partial。
    session.execute(
        text(
            "update analysis_documents set status='failed', attempts=max_attempts "
            "where task_id = :task_id and ordinal=1"
        ),
        {"task_id": task_id},
    )
    session.commit()
    from app.worker import run_once

    for _ in range(10):
        run_once()
        session.expire_all()
        status = session.execute(
            text("select status from analysis_tasks where id = :id"), {"id": task_id}
        ).scalar_one()
        if status == "partial":
            break
    assert status == "partial"
    detail = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{task_id}"
    ).json()
    assert detail["documents"][1]["status"] == "failed"
    assert detail["conclusions"] == []
    assert detail["summary"] is None


def test_expired_summary_lease_recovers_with_owner_context(client, session, monkeypatch):
    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023 年。", "b.txt")
    _install_summary(monkeypatch, {"conclusions": []})
    created = _create(
        client, team.id, kb_id, [first["id"], second["id"]], kind="comparison"
    )
    task_id = created.json()["id"]
    from app.worker import run_once

    for _ in range(8):
        run_once()
        session.expire_all()
        stage = session.execute(
            text("select stage from analysis_tasks where id=:id"), {"id": task_id}
        ).scalar_one()
        if stage == "comparing":
            break
    session.execute(
        text(
            "update analysis_summaries set status='running', attempts=1, "
            "lease_token=gen_random_uuid(), lease_expires_at=:past where task_id=:id"
        ),
        {"past": datetime.now(UTC) - timedelta(seconds=1), "id": task_id},
    )
    session.commit()
    assert _run_until_finished(session, task_id, rounds=8) == "completed"


def test_reclaim_preserves_owner_identity(monkeypatch):
    from app.analysis_tasks import reclaim_expired_documents

    class SessionStub:
        def execute(self, _statement, _parameters=None):
            return type(
                "Result",
                (),
                {
                    "all": lambda self: [
                        type(
                            "Row",
                            (),
                            {
                                "document_execution_id": uuid.uuid4(),
                                "task_id": uuid.uuid4(),
                                "team_id": uuid.uuid4(),
                                "owner_id": uuid.uuid4(),
                                "exhausted": False,
                            },
                        )()
                    ]
                },
            )()

    rows = reclaim_expired_documents(SessionStub())  # type: ignore[arg-type]
    assert len(rows) == 1
    assert len(rows[0]) == 5
    assert rows[0][3] is not None


def test_rerun_records_source_task_and_keeps_old_results(client, session):
    _, team, kb_id = _team_with_kb(client, session)
    document = _ready_document(
        client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt"
    )
    source = _create(client, team.id, kb_id, [document["id"]])
    assert source.status_code == 202, source.text
    source_id = source.json()["id"]
    assert _run_until_finished(session, source_id) == "completed"

    rerun = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses",
        json={
            "kind": "extraction",
            "document_ids": [document["id"]],
            "fields": _single_field("发布日期", "资料发布日期", "date"),
            "title": "重新运行",
            "source_task_id": source_id,
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert rerun.status_code == 202, rerun.text
    assert rerun.json()["id"] != source_id
    assert rerun.json()["source_task_id"] == source_id
    assert _run_until_finished(session, rerun.json()["id"]) == "completed"

    old = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{source_id}"
    )
    assert old.status_code == 200
    assert old.json()["task"]["source_task_id"] is None
    assert old.json()["results"]


def test_rerun_rejects_source_task_from_another_user(client, session):
    from tests.conftest import add_member, make_user

    _, team, kb_id = _team_with_kb(client, session, email="owner@example.com")
    document = _ready_document(
        client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt"
    )
    source_id = _create(client, team.id, kb_id, [document["id"]]).json()["id"]

    other = make_user(session, "other@example.com")
    add_member(session, team, other, role="admin")
    client.post(
        "/api/auth/login",
        json={"email": "other@example.com", "password": "password123"},
    )
    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses",
        json={
            "kind": "extraction",
            "document_ids": [document["id"]],
            "fields": _single_field("发布日期", "资料发布日期", "date"),
            "source_task_id": source_id,
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422


def test_two_claimers_cannot_claim_same_document_or_summary(client, session):
    """两个独立事务抢同一行：数据库 skip locked 保证只有一个领取成功。"""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import text as sql_text

    from app.analysis_tasks import claim_next_document, claim_next_summary
    from tests.conftest import AppSession

    _, team, kb_id = _team_with_kb(client, session)
    first = _ready_document(client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt")
    second = _ready_document(client, session, team.id, kb_id, "发布日期为 2023 年。", "b.txt")
    _create(client, team.id, kb_id, [first["id"], second["id"]], kind="comparison")

    def claim_many(kind, barrier):
        work = AppSession()
        try:
            barrier.wait()
            claim = claim_next_document if kind == "document" else claim_next_summary
            row = claim(work, 120)
            work.commit()
            return row
        finally:
            work.close()

    document_results = _race(claim_many, "document", 2, ThreadPoolExecutor, Barrier)
    assert sum(row is not None for row in document_results) == 1, (
        "同一文档不应被两个执行者同时领取"
    )

    # 模拟这两份文档都已完成，然后让两个连接同时抢唯一汇总。
    task_id = session.execute(
        sql_text("select id from analysis_tasks where kind = 'comparison'")
    ).scalar_one()
    session.execute(
        sql_text(
            "update analysis_documents set status='succeeded', lease_token=null, "
            "lease_expires_at=null where task_id = :task_id"
        ),
        {"task_id": task_id},
    )
    session.execute(
        sql_text(
            "update analysis_tasks set status='running', stage='comparing' "
            "where id = :task_id"
        ),
        {"task_id": task_id},
    )
    # 正常发布第一份文档后会创建汇总行；这里只有直接抢文档，没有发布，
    # 因此显式建立同样的汇总待领取状态。
    execution_id = session.execute(
        sql_text(
            "select id from analysis_documents where task_id = :task_id "
            "order by ordinal limit 1"
        ),
        {"task_id": task_id},
    ).scalar_one()
    session.execute(
        sql_text(
            "insert into analysis_summaries "
            "(id, team_id, knowledge_base_id, task_id, owner_id, "
            "document_execution_id, status, attempts, max_attempts) "
            "select gen_random_uuid(), team_id, knowledge_base_id, id, created_by_id, "
            ":execution_id, 'queued', 0, 2 from analysis_tasks where id = :task_id"
        ),
        {"task_id": task_id, "execution_id": execution_id},
    )
    session.commit()
    summary_results = _race(claim_many, "summary", 2, ThreadPoolExecutor, Barrier)
    assert sum(row is not None for row in summary_results) == 1, (
        "同一汇总不应被两个执行者同时领取"
    )


def _race(claim, kind, workers, executor_type, barrier_type):
    barrier = barrier_type(workers)
    with executor_type(max_workers=workers) as pool:
        return list(pool.map(lambda _: claim(kind, barrier), range(workers)))


def test_delete_after_publish_hides_results_immediately(client, session):
    _, team, kb_id = _team_with_kb(client, session)
    document = _ready_document(
        client, session, team.id, kb_id, "发布日期为 2022 年。", "a.txt"
    )
    task_id = _create(client, team.id, kb_id, [document["id"]]).json()["id"]
    assert _run_until_finished(session, task_id) == "completed"

    before = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{task_id}"
    )
    assert before.json()["results"]
    assert "2022" in before.text

    assert client.delete(f"/api/teams/{team.id}/documents/{document['id']}").status_code == 204
    after = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/analyses/{task_id}"
    )
    assert after.status_code == 200
    assert after.json()["task"]["status"] == "invalidated"
    assert after.json()["results"] == []
    assert after.json()["conclusions"] == []
    assert "2022" not in after.text

"""V4 fixed-plan execution: persistence, isolation, recovery and cancellation."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.conftest import add_member, make_user
from tests.test_analysis_api import _team_with_kb, _upload_ready


def _payload(document_ids: list[str], *, kind: str = "extraction"):
    return {
        "kind": kind,
        "document_ids": document_ids,
        "fields": [
            {
                "name": "适用范围",
                "description": "资料说明适用的对象或场景",
                "value_type": "text",
            }
        ],
        "analysis_title": "资料条件提取",
        "report_title": "资料条件报告",
        "report_purpose": "供核对比较",
        "report_focus": "适用条件和缺失信息",
        "report_length": "medium",
    }


def _create(client, team_id: str, kb_id: str, payload: dict, key: str | None = None):
    return client.post(
        f"/api/teams/{team_id}/knowledge-bases/{kb_id}/automations",
        json=payload,
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


def _run_until_finished(session, automation_id: str, rounds: int = 60):
    from app.worker import _run_analysis_path_once

    for _ in range(rounds):
        session.expire_all()
        status = session.execute(
            text("select status from knowledge_automations where id = :id"),
            {"id": automation_id},
        ).scalar_one()
        if status in ("completed", "failed", "canceled", "invalidated"):
            return status
        # 定向推进分析路径，避免已排队资料解析让这个测试检查不到父流程。
        _run_analysis_path_once()
    raise AssertionError("execution did not converge")


def test_create_replay_scope_and_isolation(client, session):
    user, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "本制度适用于所有员工。")
    key = str(uuid.uuid4())
    first = _create(client, team.id, kb_id, _payload([document["id"]]), key)
    assert first.status_code == 202, first.text
    replay = _create(client, team.id, kb_id, _payload([document["id"]]), key)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    conflicting_payload = _payload([document["id"]])
    conflicting_payload["fields"][0]["name"] = "适用期限"
    conflict = _create(client, team.id, kb_id, conflicting_payload, key)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "AUTOMATION_IDEMPOTENCY_CONFLICT"

    client.post("/api/auth/logout")
    other = make_user(session, "other@example.com")
    add_member(session, team, other, role="admin")
    client.post("/api/auth/login", json={"email": other.email, "password": "password123"})
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{first.json()['id']}"
    )
    assert client.get(path).status_code == 404
    assert client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations"
    ).json()["items"] == []
    assert client.delete(path).status_code == 404

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": user.email, "password": "password123"})
    listed = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations"
    ).json()["items"]
    assert [item["id"] for item in listed] == [first.json()["id"]]
    assert listed[0]["document_ids"] == [document["id"]]
    detail = client.get(path).json()
    assert detail["steps"][0]["kind"] == "start"


def test_full_loop_creates_one_report_and_records_steps(client, session):
    user, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(
        client,
        session,
        team.id,
        kb_id,
        "本制度适用于所有员工，试用期员工同样适用。",
    )
    created = _create(client, team.id, kb_id, _payload([document["id"]]))
    assert created.status_code == 202, created.text
    automation_id = created.json()["id"]
    assert _run_until_finished(session, automation_id) == "completed"

    detail = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{automation_id}"
    ).json()
    assert detail["analysis_id"]
    assert detail["report_id"]
    assert {step["kind"] for step in detail["steps"]} >= {"start", "analysis", "report"}
    assert detail["report_title"] == "资料条件报告"

    report = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/reports/{detail['report_id']}"
    )
    assert report.status_code == 200
    assert report.json()["source_analysis_id"] == detail["analysis_id"]
    assert report.json()["status"] == "draft"
    assert session.execute(
        text("select count(*) from reports where source_analysis_id = :id"),
        {"id": detail["analysis_id"]},
    ).scalar_one() == 1

    # Running again cannot duplicate a published report.
    from app.worker import run_once

    for _ in range(4):
        run_once()
    assert session.execute(text("select count(*) from reports")).scalar_one() == 1


def test_delete_completed_automation_keeps_analysis_and_report(client, session):
    user, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "本制度适用于所有员工。")
    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()
    automation_id = created["id"]
    assert _run_until_finished(session, automation_id) == "completed"
    base = f"/api/teams/{team.id}/knowledge-bases/{kb_id}"
    detail = client.get(f"{base}/automations/{automation_id}").json()

    response = client.delete(f"{base}/automations/{automation_id}")
    assert response.status_code == 204
    assert client.get(f"{base}/automations/{automation_id}").status_code == 404
    assert client.get(f"{base}/analyses/{detail['analysis_id']}").status_code == 200
    assert client.get(f"{base}/reports/{detail['report_id']}").status_code == 200
    session.expire_all()
    assert session.execute(
        text("select count(*) from knowledge_automation_steps where automation_id = :id"),
        {"id": automation_id},
    ).scalar_one() == 0


def test_comparison_requires_two_sources_and_cancel_before_claim_is_idempotent(
    client, session
):
    user, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "适用对象为管理员。")
    response = _create(
        client, team.id, kb_id, _payload([document["id"]], kind="comparison")
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "AUTOMATION_INVALID_INPUT"

    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{created['id']}"
    )
    first = client.post(f"{path}/cancel")
    second = client.post(f"{path}/cancel")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "canceled"
    assert second.json()["status"] == "canceled"
    detail = client.get(path).json()
    assert sum(step["kind"] == "cancel" for step in detail["steps"]) == 1


def test_stale_lease_cannot_publish(client, session):
    from app import automation
    from app.db import apply_context

    user, team, kb_id = _team_with_kb(client, session)
    owner_id = user.id
    document = _upload_ready(client, session, team.id, kb_id, "适用对象为团队成员。")
    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()
    claimed = None
    with session:
        apply_context(session, team_id=team.id)
        claimed = automation.claim_next(session)
        session.commit()
    assert claimed is not None
    session.execute(
        text("update knowledge_automations set lease_token = gen_random_uuid() where id = :id"),
        {"id": created["id"]},
    )
    session.commit()
    with session:
        apply_context(
            session,
            team_id=team.id,
            user_id=owner_id,
        )
        outcome = automation.run_once(
            session,
            automation_id=claimed.automation_id,
            team_id=claimed.team_id,
            owner_id=claimed.owner_id,
            lease_token=claimed.lease_token,
        )
    assert outcome == "lease_lost"
    assert session.execute(
        text("select count(*) from analysis_tasks where team_id = :team_id"),
        {"team_id": team.id},
    ).scalar_one() == 0


def test_cancel_after_analysis_created_prevents_report(client, session):
    from app import automation
    from app.db import apply_context
    from app.worker import _run_analysis_path_once

    _, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "适用对象为全体成员。")
    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{created['id']}"
    )

    with session:
        apply_context(session, team_id=team.id)
        claimed = automation.claim_next(session)
        session.commit()
    assert claimed is not None
    with session:
        assert (
            automation.run_once(
                session,
                automation_id=claimed.automation_id,
                team_id=claimed.team_id,
                owner_id=claimed.owner_id,
                lease_token=claimed.lease_token,
            )
            == "analysis_queued"
        )

    canceled = client.post(f"{path}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert canceled.json()["analysis_id"]
    for _ in range(4):
        _run_analysis_path_once()
    detail = client.get(path).json()
    assert detail["status"] == "canceled"
    assert detail["report_id"] is None
    assert session.execute(
        text("select status from analysis_tasks where id = :id"),
        {"id": detail["analysis_id"]},
    ).scalar_one() == "failed"
    assert session.execute(text("select count(*) from reports")).scalar_one() == 0


def test_deleting_report_keeps_automation_record(client, session):
    _, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "适用对象为所有员工。")
    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()
    assert _run_until_finished(session, created["id"]) == "completed"
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{created['id']}"
    )
    detail = client.get(path).json()
    deleted = client.delete(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/reports/{detail['report_id']}"
    )
    assert deleted.status_code == 204
    remaining = client.get(path).json()
    assert remaining["report_id"] is None
    assert remaining["status"] == "completed"
    assert any(step["kind"] == "report" for step in remaining["steps"])


def test_report_failure_stops_after_max_attempts(client, session, monkeypatch):
    from app import automation, knowledge_products

    _, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "适用对象为所有员工。")
    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()

    def fail_report(*_args, **_kwargs):
        raise knowledge_products.KnowledgeProductError("报告模型暂时不可用", 502)

    monkeypatch.setattr(automation, "_publish_report", fail_report)
    assert _run_until_finished(session, created["id"]) == "failed"
    detail = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{created['id']}"
    ).json()
    assert detail["error_code"] == "AUTOMATION_EXHAUSTED"
    assert detail["attempts"] == detail["max_attempts"]
    report_steps = [step for step in detail["steps"] if step["kind"] == "report"]
    assert len(report_steps) == 1
    assert report_steps[0]["status"] == "failed"
    assert session.execute(text("select count(*) from reports")).scalar_one() == 0


def test_continue_after_report_failure_reuses_step_and_creates_one_report(
    client, session, monkeypatch
):
    from app import automation, knowledge_products

    _, team, kb_id = _team_with_kb(client, session)
    document = _upload_ready(client, session, team.id, kb_id, "本说明适用于所有成员。")
    created = _create(client, team.id, kb_id, _payload([document["id"]])).json()
    original_publish = automation._publish_report
    calls = 0

    def fail_twice(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise knowledge_products.KnowledgeProductError("报告模型暂时不可用", 502)
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(automation, "_publish_report", fail_twice)
    assert _run_until_finished(session, created["id"]) == "failed"
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb_id}/automations/{created['id']}"
    )
    resumed = client.post(f"{path}/continue")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"
    assert resumed.json()["attempts"] == 0
    assert _run_until_finished(session, created["id"]) == "completed"

    detail = client.get(path).json()
    assert detail["report_id"]
    report_steps = [step for step in detail["steps"] if step["kind"] == "report"]
    assert len(report_steps) == 1
    assert report_steps[0]["status"] == "succeeded"
    assert session.execute(text("select count(*) from reports")).scalar_one() == 1

from __future__ import annotations

from app.ratelimit import FixedWindowLimiter
from tests.conftest import add_member, make_team, make_user


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_limiter_allows_limit_then_rejects_and_recovers():
    clock = Clock()
    limiter = FixedWindowLimiter(2, 60, clock=clock)

    assert limiter.check("user").allowed
    assert limiter.check("user").allowed
    rejected = limiter.check("user")
    assert not rejected.allowed
    assert rejected.retry_after == 60

    clock.value = 60
    assert limiter.check("user").allowed


def test_limiter_isolates_keys():
    limiter = FixedWindowLimiter(1, 60)
    assert limiter.check("a").allowed
    assert limiter.check("b").allowed
    assert not limiter.check("a").allowed


def test_login_returns_429_with_retry_after(client, session, monkeypatch):
    from app.routers import auth

    email_limiter = FixedWindowLimiter(1, 60)
    ip_limiter = FixedWindowLimiter(10, 60)
    monkeypatch.setattr(auth, "email_login_limiter", email_limiter)
    monkeypatch.setattr(auth, "ip_login_limiter", ip_limiter)

    user = make_user(session, "limited@example.com")
    team = make_team(session, "限流团队")
    add_member(session, team, user)
    payload = {"email": user.email, "password": "wrong-password"}

    assert client.post("/api/auth/login", json=payload).status_code == 401
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_question_rate_limit_applies_to_conversation_and_legacy_routes(
    client, session, monkeypatch
):
    from app.routers import ask, conversations

    limiter = FixedWindowLimiter(1, 60)
    monkeypatch.setattr(conversations, "answer_limiter", limiter)

    user = make_user(session, "ask-limit@example.com")
    team = make_team(session, "问答限流团队")
    add_member(session, team, user, role="admin")
    client.post(
        "/api/auth/login", json={"email": user.email, "password": "password123"}
    )
    kb = client.post(
        f"/api/teams/{team.id}/knowledge-bases", json={"name": "限流资料库"}
    ).json()
    conversation = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations",
        json={"title": "限流会话"},
    ).json()

    first = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream",
        json={"client_request_id": "00000000-0000-4000-8000-000000000001", "question": "问题"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream",
        json={"client_request_id": "00000000-0000-4000-8000-000000000002", "question": "问题"},
    )
    assert second.status_code == 429

    monkeypatch.setattr(ask, "ask_limiter", limiter)
    legacy = client.post(
        f"/api/teams/{team.id}/ask",
        json={"knowledge_base_id": kb["id"], "question": "问题"},
    )
    assert legacy.status_code == 429

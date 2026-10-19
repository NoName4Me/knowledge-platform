from __future__ import annotations

import logging


def test_request_log_excludes_query_and_body(client, caplog):
    caplog.set_level(logging.INFO, logger="kp.http")

    response = client.get("/api/health?token=must-not-appear")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert "method=GET path=/api/health status=200" in caplog.text
    assert "must-not-appear" not in caplog.text


def test_request_log_does_not_capture_login_credentials(client, session, caplog):
    from tests.conftest import add_member, make_team, make_user

    caplog.set_level(logging.INFO, logger="kp.http")
    user = make_user(session, "log-check@example.com")
    team = make_team(session, "日志团队")
    add_member(session, team, user, role="admin")

    response = client.post(
        "/api/auth/login",
        json={"email": "log-check@example.com", "password": "password123"},
    )

    assert response.status_code == 200
    assert "path=/api/auth/login status=200" in caplog.text
    assert "password123" not in caplog.text
    assert "log-check@example.com" not in caplog.text

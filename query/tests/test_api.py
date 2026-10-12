"""Unit coverage for the dashboard API's authentication and read-only boundary."""

import base64

import pytest
from fastapi import HTTPException

from query_service.api import _basic_auth, _connection, _failure_union, create_app
from query_service.config import FrontendAuth
from query_service.database import PipelineDatabase
from fastapi.testclient import TestClient


pytestmark = pytest.mark.database


class RequestStub:
    def __init__(self, authorization: str) -> None:
        self.headers = {"authorization": authorization}


def _config(path, database_path) -> None:
    path.write_text(
        f"email: person@yahoo.com\npassword: app-password\ndatabase: {database_path}\n"
        "frontend_auth:\n  username: dashboard\n  password: dashboard-password\n",
        encoding="utf-8",
    )


def _client(tmp_path):
    database = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    config = tmp_path / "values.yaml"
    _config(config, database._database.path)
    return TestClient(create_app(str(config))), database


def test_cookie_login_guards_data_and_logout_revokes_session(tmp_path):
    client, _ = _client(tmp_path)
    denied = client.get("/stats")
    assert denied.status_code == 401
    assert "www-authenticate" not in denied.headers
    assert (
        client.post(
            "/auth/login", json={"username": "dashboard", "password": "wrong"}
        ).status_code
        == 401
    )
    response = client.post(
        "/auth/login",
        json={
            "username": "dashboard",
            "password": "dashboard-password",
            "remember": True,
        },
    )
    assert response.status_code == 200
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=strict" in response.headers["set-cookie"]
    )
    token = client.cookies.get("house_session")
    assert client.get("/stats").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    client.cookies.set("house_session", token)
    assert client.get("/stats").status_code == 401


def test_login_rejects_cross_origin_and_cookie_tampering(tmp_path):
    client, _ = _client(tmp_path)
    body = {"username": "dashboard", "password": "dashboard-password"}
    assert (
        client.post(
            "/auth/login", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    client.cookies.set("house_session", "tampered.bad")
    assert client.get("/activity").status_code == 401


def test_pagination_returns_full_total_and_disjoint_pages(tmp_path):
    client, db = _client(tmp_path)
    for i in range(61):
        db.remember_if_new(str(i), str(i), "alerts@stekkies.com", f"Email {i}", b"")
    client.post(
        "/auth/login", json={"username": "dashboard", "password": "dashboard-password"}
    )
    first = client.get("/activity?kind=inbox&page=0&page_size=25").json()
    second = client.get("/activity?kind=inbox&page=1&page_size=25").json()
    last = client.get("/activity?kind=inbox&page=2&page_size=25").json()
    assert (
        first["total"] == 61 and len(first["items"]) == 25 and len(last["items"]) == 11
    )
    assert {r["id"] for r in first["items"]}.isdisjoint(
        {r["id"] for r in second["items"]}
    )


def test_api_registers_only_dashboard_routes_and_requires_frontend_auth(
    tmp_path,
) -> None:
    database_path = tmp_path / "pipeline.sqlite3"
    PipelineDatabase(database_path)
    config_path = tmp_path / "values.yaml"
    _config(config_path, database_path)

    app = create_app(str(config_path))
    paths = {route.path for route in app.routes}

    assert {
        "/health",
        "/stats",
        "/listings",
        "/applications",
        "/provider-listings/{provider_listing_id}/screenshots",
        "/failures",
    } <= paths
    assert app.docs_url is None


def test_basic_auth_rejects_invalid_credentials_and_accepts_the_configured_pair() -> (
    None
):
    auth = FrontendAuth("dashboard", "dashboard-password")

    with pytest.raises(HTTPException) as error:
        _basic_auth(RequestStub(""), auth)  # type: ignore[arg-type]
    assert error.value.status_code == 401
    encoded = base64.b64encode(b"dashboard:dashboard-password").decode()
    _basic_auth(RequestStub(f"Basic {encoded}"), auth)  # type: ignore[arg-type]


def test_api_uses_read_only_sqlite_connections_and_source_failure_union(
    tmp_path,
) -> None:
    database_path = tmp_path / "pipeline.sqlite3"
    PipelineDatabase(database_path)

    with _connection(str(database_path)) as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        with pytest.raises(Exception):
            connection.execute("CREATE TABLE should_not_be_created (id INTEGER)")

    union = _failure_union()
    assert "FROM emails WHERE status = 'error'" in union
    assert "FROM stekkies_links WHERE status = 'error'" in union
    assert "FROM provider_listings WHERE status = 'error'" in union
    assert "raw_message" not in union

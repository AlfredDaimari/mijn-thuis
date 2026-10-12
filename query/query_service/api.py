"""Authenticated, read-only HTTP API for the House Bot dashboard."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import re
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import FrontendAuth, load_settings, submission_profile, submission_problems
from .dashboard_auth import DashboardSessions, COOKIE
from .dashboard_pages import install_pages


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def _sqlite_timestamp(value: str) -> str:
    """Accept an ISO-8601 API timestamp and return SQLite's UTC text format."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid time range") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _month_bounds(start: str | None, end: str | None) -> tuple[str, str]:
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=400, detail="Provide both from and to, or neither"
        )
    if start is not None and end is not None:
        lower, upper = _sqlite_timestamp(start), _sqlite_timestamp(end)
        if lower >= upper:
            raise HTTPException(
                status_code=400, detail="The time range must be increasing"
            )
        return lower, upper
    now = datetime.now(timezone.utc)
    lower = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if lower.month == 12:
        upper = lower.replace(year=lower.year + 1, month=1)
    else:
        upper = lower.replace(month=lower.month + 1)
    return lower.strftime("%Y-%m-%d %H:%M:%S"), upper.strftime("%Y-%m-%d %H:%M:%S")


def _encode_cursor(payload: dict[str, Any]) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_cursor(cursor: str | None, expected_kind: str) -> dict[str, Any] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(status_code=400, detail="Invalid cursor") from error
    if not isinstance(decoded, dict) or decoded.get("kind") != expected_kind:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return decoded


@contextmanager
def _connection(database_path: str):
    path = Path(database_path).resolve()
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Pipeline database is unavailable")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
    finally:
        connection.close()


def _safe_diagnostic(value: str) -> str:
    """Keep useful failure text while never returning an unbounded worker trace."""
    value = re.sub(r"fill\([\s\S]*?\)\s*(?=\n|$)", "fill([redacted])", value)
    return value.replace("\x00", " ").strip()[:800]


def _website_host(value: str) -> str:
    """Return only the website host for aggregate error counts."""
    parsed = urlparse(value)
    return (parsed.hostname or value.split("/", 1)[0]).lower()


def _error_class(value: str | None) -> str:
    """Normalize free-form worker diagnostics into stable dashboard classes."""
    text = (value or "").lower()
    prefix = text.split(":", 1)[0]
    if prefix in {
        "missing_profile_field",
        "form_validation",
        "submission_unknown",
        "submission_validation_failed",
        "consent_required",
        "security_check",
        "provider_unavailable",
        "form_matching_failed",
        "submission_control_missing",
    }:
        return prefix
    if any(
        word in text
        for word in ("captcha", "cloudflare", "security checkpoint", "vercel security")
    ):
        return "security_check"
    if any(
        word in text
        for word in ("account creation", "registration", "register", "inschrijven")
    ):
        return "account_required"
    if any(word in text for word in ("login", "log in", "inloggen", "credentials")):
        return "login_required"
    if any(
        word in text
        for word in (
            "no visible contact form",
            "no recognised writable",
            "contact form",
        )
    ):
        return "contact_form_missing"
    if any(word in text for word in ("navigation", "control", "click", "iframe")):
        return "navigation_failed"
    if any(
        word in text
        for word in ("timeout", "timed out", "connection", "network", "unreachable")
    ):
        return "provider_unavailable"
    if any(word in text for word in ("field", "input", "form")):
        return "form_matching_failed"
    return "other"


def _page(
    limit: int, cursor: str | None, kind: str
) -> tuple[int, dict[str, Any] | None]:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return limit, _decode_cursor(cursor, kind)


def _basic_auth(request: Request, credentials: FrontendAuth) -> None:
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")
    try:
        decoded = (
            base64.b64decode(encoded, validate=True).decode("utf-8")
            if scheme.lower() == "basic"
            else ""
        )
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        username, password = "", ""
    if not (
        secrets.compare_digest(username, credentials.username)
        and secrets.compare_digest(password, credentials.password)
    ):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="House Bot"'},
        )


def _failure_union() -> str:
    return """
        SELECT 'emails' AS source_table, id AS source_id, processed_at, accessed_at,
               attempt_count, processing_error, NULL AS source_url, subject AS label
        FROM emails WHERE status = 'error' AND processed_at >= ? AND processed_at < ?
        UNION ALL
        SELECT 'stekkies_links' AS source_table, id AS source_id, processed_at, accessed_at,
               attempt_count, processing_error, stekkies_url AS source_url, title AS label
        FROM stekkies_links WHERE status = 'error' AND processed_at >= ? AND processed_at < ?
        UNION ALL
        SELECT 'provider_listings' AS source_table, id AS source_id, processed_at, accessed_at,
               attempt_count, processing_error, provider_url AS source_url, provider_title AS label
        FROM provider_listings WHERE status = 'error' AND processed_at >= ? AND processed_at < ?
    """


def _failure_params(start: str, end: str) -> tuple[str, ...]:
    return (start, end, start, end, start, end)


def create_app(config_path: str | None = None) -> FastAPI:
    """Create the API app; used by Uvicorn's factory mode and API tests."""
    settings = load_settings(
        config_path or os.environ.get("HOUSE_BOT_CONFIG", "/app/values.yaml")
    )
    if settings.frontend_auth is None:
        raise RuntimeError(
            "values.yaml requires frontend_auth before the dashboard API can start"
        )
    credentials = settings.frontend_auth
    sessions = DashboardSessions(credentials)

    app = FastAPI(
        title="House Bot API", docs_url=None, redoc_url=None, openapi_url=None
    )
    sessions.install(app)
    install_pages(
        app,
        settings.database,
        _connection,
        _month_bounds,
        _error_class,
        _failure_union,
        _safe_diagnostic,
    )

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Callable):  # type: ignore[type-arg]
        if request.url.path not in {
            "/auth/login",
            "/auth/session",
        } and not sessions.valid(request.cookies.get(COOKIE)):
            return JSONResponse(status_code=401, content={"detail": "Sign in required"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        with _connection(settings.database) as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/pipeline-status")
    def pipeline_status():
        current = load_settings(
            config_path or os.environ.get("HOUSE_BOT_CONFIG", "/app/values.yaml")
        )
        problems = submission_problems(submission_profile(current))
        return {"submission_ready": not problems, "problems": problems}

    @app.get("/stats")
    def stats(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        tables = ("emails", "stekkies_links", "provider_listings")
        with _connection(settings.database) as connection:
            sources = {
                table: {
                    row["status"]: row["count"]
                    for row in connection.execute(
                        f"SELECT status, COUNT(*) AS count FROM {table} WHERE accessed_at >= ? AND accessed_at < ? GROUP BY status",
                        (lower, upper),
                    )
                }
                for table in tables
            }
            failures = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE status = 'error' AND processed_at >= ? AND processed_at < ?",
                    (lower, upper),
                ).fetchone()[0]
                for table in tables
            }
        return {
            "period": {"from": lower, "to": upper},
            "sources": sources,
            "failures": failures,
        }

    @app.get("/emails")
    def emails(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        size, _ = _page(limit, None, "emails")
        with _connection(settings.database) as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT id, accessed_at, sender, subject, status, processed_at,
                          processing_error, attempt_count
                   FROM emails WHERE accessed_at >= ? AND accessed_at < ?
                   ORDER BY accessed_at DESC, id DESC LIMIT ?""",
                    (lower, upper, size),
                ).fetchall()
            ]
        return {"items": rows, "next_cursor": None}

    @app.get("/stekkies-links")
    def stekkies_links(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        size, _ = _page(limit, None, "stekkies_links")
        with _connection(settings.database) as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT id, email_id, accessed_at, stekkies_url, title,
                          room_count, status, processed_at, processing_error,
                          attempt_count, provider_listing_id
                   FROM stekkies_links WHERE accessed_at >= ? AND accessed_at < ?
                   ORDER BY accessed_at DESC, id DESC LIMIT ?""",
                    (lower, upper, size),
                ).fetchall()
            ]
        return {"items": rows, "next_cursor": None}

    @app.get("/provider-listings")
    def provider_listings(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        size, _ = _page(limit, None, "provider_listings")
        with _connection(settings.database) as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT id, accessed_at, provider_url, provider_title, location,
                          monthly_rent_cents, area_m2, room_count, status,
                          processed_at, processing_error, attempt_count, screenshot_id
                   FROM provider_listings WHERE accessed_at >= ? AND accessed_at < ?
                   ORDER BY accessed_at DESC, id DESC LIMIT ?""",
                    (lower, upper, size),
                ).fetchall()
            ]
        return {"items": rows, "next_cursor": None}

    @app.get("/listings")
    def listings(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        size, token = _page(limit, cursor, "listings")
        query = """
            SELECT l.id AS stekkies_link_id, l.accessed_at, l.stekkies_url, l.title AS source_title,
                   l.room_count AS source_room_count, l.status AS link_status,
                   e.id AS email_id, e.status AS email_status,
                   p.id AS provider_listing_id, p.provider_url, p.provider_title, p.location,
                   p.monthly_rent_cents, p.area_m2, p.room_count, p.status AS provider_status
            FROM stekkies_links AS l
            JOIN emails AS e ON e.id = l.email_id
            LEFT JOIN provider_listings AS p ON p.id = l.provider_listing_id
        """
        query += " WHERE l.accessed_at >= ? AND l.accessed_at < ?"
        params: list[Any] = [lower, upper]
        if token:
            try:
                query += " AND (l.accessed_at < ? OR (l.accessed_at = ? AND l.id < ?))"
                params.extend((token["at"], token["at"], int(token["id"])))
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(status_code=400, detail="Invalid cursor") from error
        query += " ORDER BY l.accessed_at DESC, l.id DESC LIMIT ?"
        params.append(size + 1)
        with _connection(settings.database) as connection:
            rows = [dict(row) for row in connection.execute(query, params).fetchall()]
        next_cursor = None
        if len(rows) > size:
            rows.pop()
            final = rows[-1]
            next_cursor = _encode_cursor(
                {
                    "kind": "listings",
                    "at": final["accessed_at"],
                    "id": final["stekkies_link_id"],
                }
            )
        return {"items": rows, "next_cursor": next_cursor}

    @app.get("/applications")
    def applications(
        status: Literal["submitted"] = "submitted",
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        size, token = _page(limit, cursor, "applications")
        query = """
            SELECT id AS provider_listing_id, provider_url, provider_title, location,
                   monthly_rent_cents, area_m2, room_count, status, processed_at, screenshot_id
            FROM provider_listings WHERE status = ? AND processed_at >= ? AND processed_at < ?
        """
        params: list[Any] = [status, lower, upper]
        if token:
            try:
                query += " AND (processed_at < ? OR (processed_at = ? AND id < ?))"
                params.extend((token["at"], token["at"], int(token["id"])))
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(status_code=400, detail="Invalid cursor") from error
        query += " ORDER BY processed_at DESC, id DESC LIMIT ?"
        params.append(size + 1)
        with _connection(settings.database) as connection:
            rows = [dict(row) for row in connection.execute(query, params).fetchall()]
        next_cursor = None
        if len(rows) > size:
            rows.pop()
            final = rows[-1]
            next_cursor = _encode_cursor(
                {
                    "kind": "applications",
                    "at": final["processed_at"],
                    "id": final["provider_listing_id"],
                }
            )
        return {"items": rows, "next_cursor": next_cursor}

    @app.get("/provider-listings/{provider_listing_id}/screenshots")
    def screenshots(
        provider_listing_id: int,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        size, token = _page(limit, cursor, "screenshots")
        query = """
            SELECT id AS screenshot_id, provider_listing_id, attempt_count, stage, nginx_path, captured_at
            FROM screenshots WHERE provider_listing_id = ?
        """
        params: list[Any] = [provider_listing_id]
        if token:
            try:
                query += " AND (captured_at > ? OR (captured_at = ? AND id > ?))"
                params.extend((token["at"], token["at"], int(token["id"])))
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(status_code=400, detail="Invalid cursor") from error
        query += " ORDER BY captured_at ASC, id ASC LIMIT ?"
        params.append(size + 1)
        with _connection(settings.database) as connection:
            rows = [dict(row) for row in connection.execute(query, params).fetchall()]
        next_cursor = None
        if len(rows) > size:
            rows.pop()
            final = rows[-1]
            next_cursor = _encode_cursor(
                {
                    "kind": "screenshots",
                    "at": final["captured_at"],
                    "id": final["screenshot_id"],
                }
            )
        return {"items": rows, "next_cursor": next_cursor}

    @app.get("/failures")
    def failures(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        lower, upper = _month_bounds(start, end)
        size, token = _page(limit, cursor, "failures")
        union = _failure_union()
        query = f"SELECT * FROM ({union})"
        params: list[Any] = list(_failure_params(lower, upper))
        if token:
            try:
                query += " WHERE (processed_at < ? OR (processed_at = ? AND (source_table > ? OR (source_table = ? AND source_id < ?))))"
                params.extend(
                    (
                        token["at"],
                        token["at"],
                        token["table"],
                        token["table"],
                        int(token["id"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise HTTPException(status_code=400, detail="Invalid cursor") from error
        query += " ORDER BY processed_at DESC, source_table ASC, source_id DESC LIMIT ?"
        params.append(size + 1)
        with _connection(settings.database) as connection:
            rows = [dict(row) for row in connection.execute(query, params).fetchall()]
            summary_rows = connection.execute(
                f"SELECT source_table, processing_error, COUNT(*) AS count FROM ({union}) GROUP BY source_table, processing_error ORDER BY count DESC, source_table ASC",
                _failure_params(lower, upper),
            ).fetchall()
            url_rows = connection.execute(
                f"SELECT source_table, source_url, processing_error, COUNT(*) AS count FROM ({union}) WHERE source_url IS NOT NULL GROUP BY source_table, source_url, processing_error",
                _failure_params(lower, upper),
            ).fetchall()
            class_source_rows = connection.execute(
                f"SELECT source_table, processing_error, COUNT(*) AS count FROM ({union}) GROUP BY source_table, processing_error",
                _failure_params(lower, upper),
            ).fetchall()
        website_counts: dict[tuple[str, str], int] = {}
        website_class_counts: dict[tuple[str, str], dict[str, int]] = {}
        for row in url_rows:
            key = (row["source_table"], _website_host(row["source_url"]))
            website_counts[key] = website_counts.get(key, 0) + row["count"]
            classes = website_class_counts.setdefault(key, {})
            error_class = _error_class(row["processing_error"])
            classes[error_class] = classes.get(error_class, 0) + row["count"]
        website_rows = sorted(
            (
                {
                    "source_table": table,
                    "source_url": website,
                    "count": count,
                    "by_class": [
                        {"error_class": error_class, "count": class_count}
                        for error_class, class_count in sorted(
                            website_class_counts[(table, website)].items(),
                            key=lambda item: (-item[1], item[0]),
                        )
                    ],
                }
                for (table, website), count in website_counts.items()
            ),
            key=lambda row: (-row["count"], row["source_table"], row["source_url"]),
        )
        class_counts: dict[tuple[str, str], int] = {}
        for row in class_source_rows:
            key = (row["source_table"], _error_class(row["processing_error"]))
            class_counts[key] = class_counts.get(key, 0) + row["count"]
        class_rows = sorted(
            (
                {"source_table": table, "error_class": error_class, "count": count}
                for (table, error_class), count in class_counts.items()
            ),
            key=lambda row: (-row["count"], row["source_table"], row["error_class"]),
        )
        next_cursor = None
        if len(rows) > size:
            rows.pop()
            final = rows[-1]
            next_cursor = _encode_cursor(
                {
                    "kind": "failures",
                    "at": final["processed_at"],
                    "table": final["source_table"],
                    "id": final["source_id"],
                }
            )
        for row in rows:
            row["processing_error"] = _safe_diagnostic(row["processing_error"])
            row["error_class"] = _error_class(row["processing_error"])
        return {
            "period": {"from": lower, "to": upper},
            "items": rows,
            "next_cursor": next_cursor,
            "summary": {
                "by_reason": [
                    {
                        "source_table": row["source_table"],
                        "processing_error": _safe_diagnostic(row["processing_error"]),
                        "count": row["count"],
                    }
                    for row in summary_rows
                ],
                "by_url": website_rows,
                "by_class": class_rows,
            },
        }

    @app.exception_handler(sqlite3.Error)
    async def sqlite_error(_request: Request, _error: sqlite3.Error) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": "Pipeline database is unavailable"}
        )

    return app

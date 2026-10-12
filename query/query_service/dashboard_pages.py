"""Read-only server pagination for inbox, listings, submissions and evidence."""
from typing import Literal
from fastapi import Query


def install_pages(
    app, database, connection_factory, bounds, error_class, failure_union, diagnostic
):
    def result(connection, select, where, params, order, page, size):
        total = connection.execute(
            "SELECT COUNT(*) FROM (" + select + where + ")", params
        ).fetchone()[0]
        rows = [
            dict(r)
            for r in connection.execute(
                select + where + order + " LIMIT ? OFFSET ?",
                [*params, size, page * size],
            )
        ]
        for row in rows:
            if "processing_error" in row:
                row["error_class"] = (
                    error_class(row["processing_error"])
                    if row["processing_error"]
                    else None
                )
                if row["processing_error"]:
                    row["processing_error"] = diagnostic(row["processing_error"])
        return {"items": rows, "total": total, "page": page, "page_size": size}

    @app.get("/activity")
    def activity(
        kind: Literal["inbox", "listings", "applications", "evidence"] = "listings",
        page: int = Query(0, ge=0),
        page_size: int = Query(25, ge=1, le=100),
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
        status: str | None = None,
        search: str = Query("", max_length=200),
    ):
        lower, upper = bounds(start, end)
        if kind == "inbox":
            select = """SELECT e.id,e.accessed_at,e.sender,e.subject,e.status,e.processing_error,e.attempt_count,
                (SELECT COUNT(*) FROM stekkies_links s WHERE s.email_id=e.id) AS link_count,
                (SELECT COUNT(*) FROM stekkies_links s WHERE s.email_id=e.id AND s.status='error') AS link_errors FROM emails e"""
            where = " WHERE e.accessed_at >= ? AND e.accessed_at < ?"
            params = [lower, upper]
            if status:
                where += " AND e.status=?"
                params.append(status)
            if search:
                where += " AND (e.subject LIKE ? OR e.sender LIKE ?)"
                params.extend([f"%{search}%"] * 2)
            order = " ORDER BY e.accessed_at DESC,e.id DESC"
        else:
            select = """SELECT p.id,p.id AS provider_listing_id,p.provider_url,p.provider_title,p.location,
                p.monthly_rent_cents,p.area_m2,p.room_count,p.status,p.processing_error,p.accessed_at,p.processed_at,
                p.attempt_count,p.screenshot_id,p.submission_started_at,p.submission_confirmed_at,
                (SELECT COUNT(*) FROM screenshots s WHERE s.provider_listing_id=p.id) AS evidence_count FROM provider_listings p"""
            column = "p.processed_at" if kind == "applications" else "p.accessed_at"
            where = f" WHERE {column} >= ? AND {column} < ?"
            params = [lower, upper]
            if kind == "applications":
                where += " AND p.status='submitted'"
            if kind == "evidence":
                where += " AND EXISTS(SELECT 1 FROM screenshots s WHERE s.provider_listing_id=p.id)"
            if status and kind != "applications":
                where += " AND p.status=?"
                params.append(status)
            if search:
                where += " AND (p.provider_title LIKE ? OR p.provider_url LIKE ? OR p.location LIKE ?)"
                params.extend([f"%{search}%"] * 3)
            order = (
                " ORDER BY "
                + (
                    "COALESCE(p.processed_at,p.accessed_at)"
                    if kind == "evidence"
                    else column
                )
                + " DESC,p.id DESC"
            )
        with connection_factory(database) as connection:
            return result(connection, select, where, params, order, page, page_size)

    @app.get("/inbox/{email_id}/links")
    def inbox_links(
        email_id: int,
        page: int = Query(0, ge=0),
        page_size: int = Query(25, ge=1, le=100),
    ):
        select = """SELECT s.id,s.title,s.stekkies_url,s.status,s.processing_error,s.attempt_count,
            p.id AS provider_listing_id,p.provider_url,p.status AS provider_status
            FROM stekkies_links s LEFT JOIN provider_listings p ON p.id=s.provider_listing_id"""
        with connection_factory(database) as connection:
            return result(
                connection,
                select,
                " WHERE s.email_id=?",
                [email_id],
                " ORDER BY s.id DESC",
                page,
                page_size,
            )

    @app.get("/failure-page")
    def failure_page(
        source: Literal[
            "emails", "stekkies_links", "provider_listings"
        ] = "provider_listings",
        website: str = "",
        category: str = "",
        page: int = Query(0, ge=0),
        page_size: int = Query(25, ge=1, le=100),
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
    ):
        from urllib.parse import urlsplit

        lower, upper = bounds(start, end)
        with connection_factory(database) as connection:
            rows = [
                dict(r)
                for r in connection.execute(
                    "SELECT * FROM ("
                    + failure_union()
                    + ") WHERE source_table=? ORDER BY processed_at DESC,source_id DESC",
                    [lower, upper, lower, upper, lower, upper, source],
                )
            ]
        for row in rows:
            row["error_class"] = error_class(row["processing_error"])
            row["processing_error"] = diagnostic(row["processing_error"] or "")
        if website:
            rows = [
                r for r in rows if urlsplit(r["source_url"] or "").hostname == website
            ]
        if category:
            rows = [r for r in rows if r["error_class"] == category]
        return {
            "items": rows[page * page_size : (page + 1) * page_size],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
        }

    @app.get("/evidence/{listing_id}")
    def evidence(
        listing_id: int,
        page: int = Query(0, ge=0),
        page_size: int = Query(25, ge=1, le=100),
    ):
        select = "SELECT id AS screenshot_id,provider_listing_id,attempt_count,stage,nginx_path,captured_at FROM screenshots"
        with connection_factory(database) as connection:
            return result(
                connection,
                select,
                " WHERE provider_listing_id=?",
                [listing_id],
                " ORDER BY captured_at DESC,id DESC",
                page,
                page_size,
            )

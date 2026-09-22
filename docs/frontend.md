# Frontend and API plan

## Scope and boundaries

The frontend will be a small React + TypeScript application using Material UI.
It is a read-only operational view of the housing pipeline at first: it may
show listings, application state, errors, and evidence, but it must never read
SQLite or the screenshot directory directly. A local Python API owns all
database reads and runs behind Nginx at `/api/`.

No frontend or API implementation is included in this change. This document is
the implementation contract for the later work.

## Access control and HTTPS

The site will use HTTP Basic Authentication before any listing data or
screenshots are exposed. The ignored `values.yaml` will gain this configuration
when the API/Nginx deployment is built:

```yaml
frontend_auth:
  username: "change-me"
  password: "change-me"
```

The password is a secret and must never be committed, embedded in the frontend
bundle, returned by the API, or logged. Deployment will create a root-readable
`htpasswd` file on the VPS from it, then Nginx will enforce the same auth realm
for `/`, `/api/`, and `/screenshots/`. The API still independently rejects
unauthenticated requests; Nginx is not the only authorization boundary.

The canonical public origin will be HTTPS, using
`https://vps-24228540.vps.ovh.net/` unless a custom domain is added. The
hostname currently resolves to this VPS. Let’s Encrypt HTTP-01 can validate it
when port 80 is publicly reachable and Nginx serves the ACME challenge for that
hostname. The HTTPS deployment will:

1. request and automatically renew a Let’s Encrypt certificate with Certbot;
2. serve `/.well-known/acme-challenge/` over port 80 only for validation;
3. redirect every other HTTP request to HTTPS;
4. listen on 443 with the certificate, secure protocol settings, and HSTS only
   after HTTPS has been verified; and
5. make the screenshot alias private behind the same Basic Auth policy.

If HTTP-01 issuance is rejected by an OVH CAA policy or the hostname changes,
use a custom domain whose DNS is controlled by the account owner. Do not use a
self-signed certificate for the real dashboard: it creates browser warnings
and does not prove the server identity.

## Four tabs

| Tab | Data shown | Primary source | Notes |
| --- | --- | --- | --- |
| Listings | Every extracted candidate, its queue/application status, provider URL, title, location, rooms, monthly rent, and area. | `listings`, `resolved_listings`, `listing_details`, `applications` | Starts as the main inbox, including unresolved candidates. |
| Applied | Only rows with `applications.status = submitted`; later include submission timestamp and confirmation evidence. | `applications`, `listing_details`, `application_screenshots` | `awaiting_review` is not an application submission and must not appear as applied. |
| Evidence | Before-fill, after-fill, before-submit, and after-submit screenshots for one selected legitimate application. | `application_screenshots` | The API returns Nginx-relative image paths only after authenticating the caller. |
| Failed | At the bottom of the tab, application failures and separate worker/pipeline errors, newest first. | `applications.error`, `pipeline_errors` | Show retry count, timestamp, source URL, and safe diagnostic text; never credentials or raw email. |

Each listing uses the stable `resolved_listings.id` once it has a provider URL.
The UI must label missing provider metadata as “Unknown”, rather than inferring
or fabricating a location, price, or room count.

## API shape and pagination

The API will be implemented as a separate local container listening only on
`127.0.0.1:8000`; Nginx proxies `/api/` to it. All list endpoints use
server-side cursor pagination, never a database dump into the browser.

| Endpoint | Cursor sort | Page size | Response fields |
| --- | --- | --- | --- |
| `GET /api/listings` | `(created_at DESC, resolved_listing_id DESC)` | default 25, max 100 | `items`, `next_cursor` |
| `GET /api/applications?status=submitted` | `(completed_at DESC, resolved_listing_id DESC)` | default 25, max 100 | `items`, `next_cursor` |
| `GET /api/applications/{resolved_listing_id}/screenshots` | `(captured_at ASC, screenshot_id ASC)` | default 25, max 100 | `items`, `next_cursor` |
| `GET /api/failures` | `(last_occurred_at DESC, error_key DESC)` | default 25, max 100 | `items`, `next_cursor` |

The cursor is opaque, URL-safe, and encodes the exact final sort values; it is
not an offset. This prevents rows being skipped or duplicated when workers add
new listings while the user is paging. Every query must apply a deterministic
tie-breaker (`resolved_listing_id`, screenshot ID, or error key) and a matching
SQLite index. The response also carries a small `summary` count for tab badges,
but pagination controls never depend on totals being exact during active work.

The API accepts an optional `limit` only within 1–100. Invalid, malformed, or
stale cursors return a 400 response with a generic explanation. It will not
return raw emails, Yahoo credentials, Stekkies cookies, provider passwords,
application messages, or unredacted browser traces.

## UI behaviour

Material UI supplies an accessible app bar, responsive tabs, filter chips,
status badges, card/table layouts, loading skeletons, and empty/error states.
On narrow screens the listings table becomes compact cards. The interface uses
neutral status language: `resolved`, `awaiting review`, `submitted`, and
`failed`; it never shows “applied” until the provider submission is durably
recorded.

The evidence tab shows the provider URL, capture stage, capture time, and an
image preview. Images are loaded only from authenticated `/screenshots/` paths
and opened at natural size in a dialog. A missing image renders an explicit
“evidence file unavailable” state rather than a broken silent preview.

## Delivery order

1. Add HTTPS plus Basic Auth to the Nginx deployment and verify renewal.
2. Implement read-only API authentication, cursor queries, and API tests.
3. Build the Material UI shell and the Listings tab against mock API data.
4. Add Applied, Evidence, and Failed tabs with pagination and empty/error
   states.
5. Connect to the VPS only after API, auth, and browser tests pass locally.

Automatic form submission remains a separate safety decision. This frontend
plan does not change the current worker’s no-submit behaviour.

# Frontend and API plan

The implemented dashboard now uses a dedicated sign-in page and cookie sessions, a combined Inbox, server-paginated Listings/Applications/Evidence, and website/class failure drilldowns. The earlier planning details below are retained as design history. See [the current repair handoff](2026-10-12-repair-handoff.md) for implemented behavior and verification.

## Scope and boundaries

The frontend is a small React + TypeScript application using Material UI. It is
a read-only operational view of the housing pipeline: it may show listings,
application state, errors, and evidence, but it never reads SQLite or the
screenshot directory directly. A private FastAPI container owns database reads;
the Docker frontend Nginx exposes it only behind `/api/`.

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

The canonical public origin is HTTPS at
`https://vps-24228540.vps.ovh.net/` unless a custom domain is added. Let’s
Encrypt HTTP-01 is configured through `ansible/deploy-https.sh`; Certbot uses
the Nginx-served `/var/www/certbot` webroot and its systemd renewal timer. The
deployment:

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

## Five tabs

| Tab | Data shown | Primary source | Notes |
| --- | --- | --- | --- |
| Overview | Current selected-month pipeline counts, including submitted, awaiting-review, and failed. | `emails`, `stekkies_links`, `provider_listings` | Uses neutral status labels and exact dynamic failure counts. |
| Listings | Every extracted candidate, its email/link/application state, provider URL, title, location, rooms, monthly rent, and area. | `emails`, `stekkies_links`, `provider_listings` | Starts as the main inbox, including links not yet resolved. |
| Applied | Only rows with `provider_listings.status = submitted`; later include submission timestamp and confirmation evidence. | `provider_listings`, `screenshots` | `awaiting_review` is not an application submission and must not appear as applied. |
| Evidence | Before-fill, after-fill, before-submit, and after-submit screenshots for one selected provider listing attempt. | `screenshots` | The API returns Nginx-relative image paths only after authenticating the caller. |
| Failed | Row-level email/link/provider errors plus a selected-period summary, newest first. | Source-table `processing_error` fields | The API dynamically queries the three source tables for the selected month; the frontend shows the returned URL/error counts, retry count, timestamp, source URL where available, and safe diagnostic text. Never expose credentials or raw email. |

Each provider listing uses the stable `provider_listings.id` once resolution
succeeds. Its optional `screenshot_id` identifies the latest useful evidence;
the full evidence history is in `screenshots`.
The UI must label missing provider metadata as “Unknown”, rather than inferring
or fabricating a location, price, or room count.

## API shape and pagination

The API runs in a private Compose service; the frontend Nginx is its only HTTP
peer. All list endpoints use
server-side cursor pagination, never a database dump into the browser.

| Endpoint | Cursor sort | Page size | Response fields |
| --- | --- | --- | --- |
| `GET /api/stats?from=&to=` | — | — | source status counts and exact failure counts |
| `GET /api/listings?from=&to=` | `(accessed_at DESC, stekkies_link_id DESC)` | default 25, max 100 | `items`, `next_cursor` |
| `GET /api/applications?status=submitted&from=&to=` | `(processed_at DESC, provider_listing_id DESC)` | default 25, max 100 | `items`, `next_cursor` |
| `GET /api/provider-listings/{provider_listing_id}/screenshots` | `(captured_at ASC, screenshot_id ASC)` | default 25, max 100 | `items`, `next_cursor` |
| `GET /api/failures?from=&to=` | `(processed_at DESC, source_table, source_id DESC)` | default 25, max 100 | `items`, `next_cursor` |

The cursor is opaque, URL-safe, and encodes the exact final sort values; it is
not an offset. This prevents rows being skipped or duplicated when workers add
new listings while the user is paging. Every query must apply a deterministic
tie-breaker (`provider_listing_id`, screenshot ID, or source ID) and a matching
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
3. Build the Material UI shell and the Listings tab against the API.
4. Add Overview, Applied, Evidence, and Failed tabs with pagination and
   empty/error states.
5. Connect to the VPS only after API, auth, and browser tests pass locally.

Automatic form submission remains a separate safety decision. This frontend
plan does not change the current worker’s no-submit behaviour.

# Docker pipeline and database design

## Goal

Run four independently restartable workers on one OVH VPS. They share a local
SQLite/WAL file at `/app/data/pipeline.sqlite3`; browser screenshots live in
`/srv/house-bot/screenshots` and are served by Nginx. This is deliberately a
single-host design—never mount its SQLite volume over NFS, SMB, or another
network filesystem.

```text
Yahoo poller ──> emails ──> extractor ──> stekkies_links ──> resolver
                                                              │
                                                              v
                                                    provider_listings
                                                              │
                                                              v
                                                   screenshots (Nginx files)

emails / stekkies_links / provider_listings ──> error_summaries (derived every 2h)
```

The frontend must use a backend API; it never mounts the SQLite volume or
reads screenshots from the filesystem directly. See [frontend.md](frontend.md)
for the dashboard, authentication, HTTPS, and pagination plan.

## Database: five deliberately small tables

| Table | Source of truth | Key fields |
| --- | --- | --- |
| `emails` | A Yahoo message fetched by the poller. | `id`, unique `signature`, optional unique `message_id`, raw email, `accessed_at`, `status`, `processed_at`, `processing_error`, claim count/lease. |
| `stekkies_links` | One actionable Stekkies link extracted from an email. | `id`, `email_id`, unique `(email_id, stekkies_url)`, optional `provider_listing_id`, title/room count, `status`, result/error, claim count/lease. |
| `provider_listings` | One canonical provider URL: the actual house/application record. | `id`, unique `provider_url`, provider details, application `status`, retries/lease, local error, and latest `screenshot_id`. |
| `screenshots` | Immutable browser evidence for a provider listing attempt. | `id`, `provider_listing_id`, attempt number, stage, Nginx-relative path, capture time. |
| `error_summaries` | A rebuildable observability projection, not a worker error log. | source table, error fingerprint/message, count, first/last failure, refresh time. |

Only `emails`, `stekkies_links`, and `provider_listings` own error messages.
Their failures are directly inspectable at row level. `error_summaries` is
periodically regenerated from those three tables so the observability UI can
show a quick error count without scanning all history.

Processing states are explicit:

- Emails: `pending`, `processing`, `processed`, or `error`.
- Stekkies links: `pending`, `processing`, `processed`, or `error`.
- Provider listings: `pending`, `processing`, `awaiting_review`, `submitted`,
  or `error`.

Every worker atomically claims exactly one `pending` row by setting `status`
to `processing`. Its completion transaction creates/reuses the next-table row
and changes the claimed source row to `processed`; any exception changes that
same source row to `error` with `processing_error`. `error` rows are terminal:
no normal worker query reclaims them. Requeueing is a deliberate operator
action after the cause is understood. An abandoned `processing` lease is also
made terminal `error`, rather than silently retried. `processed_at` is set on
every terminal transition, and `(status, id)` indexes keep claims bounded.

The prior `seen_emails`, `listings`, `resolved_listings`, `applications`,
`listing_details`, `application_screenshots`, `provider_sources`, and
`pipeline_errors` tables are migrated at startup in one transaction into this
model and then removed. The migration preserves the raw messages, links,
provider URL, available details, state, and failure information. New code must
only use the five tables above.

## Worker responsibilities

| Worker | Command | Reads | Writes |
| --- | --- | --- | --- |
| Yahoo poller | `python -m query_service` | Yahoo IMAP | inserts `emails` as `pending` |
| Extractor | `python -m query_service.processor` | claims pending `emails` | inserts deduplicated `stekkies_links`, then marks email `processed` or `error` |
| Resolver | `python -m query_service.resolver` | claims pending `stekkies_links` | inserts/reuses `provider_listings`, then marks link `processed` or `error` |
| Provider review worker | `python -m query_service.application_worker` | claims pending `provider_listings` | fills/captures evidence, then marks `awaiting_review` or `error` and writes `screenshots` |

Each handoff is one short SQLite transaction: create or find the downstream
row, then mark the claimed source row `processed`. No transaction is held during IMAP,
Playwright navigation, provider login, or screenshot capture.

The resolver may extract explicit provider title, location, rent in cents,
area, and room count into the provider-listing row. Unknown values stay `NULL`;
the service must not invent data from an unlabelled price or address.

The provider worker is still **no-submit**. It may reach `awaiting_review`, but
only a later reviewed submission capability may write `submitted`.

## Screenshots

The worker saves a file atomically under `/app/screenshots`, which is the host
directory `/srv/house-bot/screenshots`. It then records a `screenshots` row in
the same short transaction that updates `provider_listings.screenshot_id`.

Each screenshot is linked to a provider listing, an attempt count, and a stage:
`capture`, `before_fill`, `after_fill`, `before_submit`, `after_submit`, or
`failure`. Multiple attempts retain their earlier evidence rather than
overwriting it. Nginx paths must be `/screenshots/<safe-file-name>` only.

## Error summaries

Run the lightweight summarizer every two hours, separately from the four live
pipeline workers:

```bash
python -m query_service.error_summary --config values.yaml --once
```

`query/run-error-summary.sh` is a convenience wrapper for this one-shot
command; schedule that script with the host scheduler at a two-hour interval.

For a continuous local scheduler, it defaults to two hours:

```bash
python -m query_service.error_summary --config values.yaml
```

It deletes and rebuilds `error_summaries` from the three source tables in one
transaction. It does not modify pipeline state, retry a failed listing, or
store credentials, cookies, raw email bodies, or application messages.

## OpenRouter fallback

The form worker uses deterministic Playwright/provider-adapter logic first. It
makes an LLM request only when that logic cannot find a safe form/navigation
path. Put an OpenRouter key—not a Gemini key or a direct OpenAI key—in the
ignored `values.yaml`:

```yaml
openrouter_api_key: "sk-or-v1-..."
openrouter_model: "openai/gpt-6-luna"
```

The client calls `https://openrouter.ai/api/v1`; the installed `openai` Python
package is solely the OpenAI-compatible HTTP client, not the billing or key
provider. The request contains only the provider hostname, generic-match
failure reason, and redacted visible navigation candidates. It never includes
the OpenRouter key in logs, or sends cookies, provider credentials, applicant
data, messages, or full page HTML. GPT-6 Luna returns a schema-checked plan
containing at most one allowed `click` or `login` candidate. Playwright checks
that candidate again before using it, and never receives a model instruction
to submit, register, consent, pay, solve a CAPTCHA, or enter form values.

If the page visibly offers account creation/registration (including Dutch
controls such as **Inschrijven**), the worker stops before an LLM call or
click. It records that account creation is required on the provider-listing
row. Create that provider account manually, then add its credentials to the
ignored `accounts.yaml` before a later, deliberate retry. An existing-account
**Inloggen** path remains separate and can use only credentials explicitly
configured for that provider.

## SQLite and deployment rules

- Use exactly one replica of each of the four live workers initially.
- SQLite WAL permits many readers and one short writer. Every data-layer method
  opens/closes its own connection, sets a busy timeout, and retries brief lock
  contention.
- Mount the whole `/app/data` directory, not only `pipeline.sqlite3`, because
  SQLite needs its `-wal` and `-shm` sidecar files.
- Back up through SQLite's online backup API, or stop writers first. Never copy
  only the main database file while workers are active.
- The error summarizer is a scheduled read/derive task; it is intentionally not
  a fifth always-running business worker.

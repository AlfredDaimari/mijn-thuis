# Docker pipeline migration guide

## Goal

Run the housing workflow as four independently restartable containers on the
same Docker host. They share one local SQLite pipeline database, while browser
screenshots live in a separate persistent volume.

This is deliberately a **single-host** design. It is the appropriate shape for
the existing OVH VPS and low-volume workflow. Do not use this design with a
network filesystem or with workers spread across Docker hosts.

```text
                            house-query-data volume
                         /app/data/pipeline.sqlite3

 Yahoo poller ───> seen_emails ───> Stekkies extractor ───> listings
                                                               |
                                                               v
                                      Chromium resolver <── Stekkies links
                                                               |
                                                               v
                                                        resolved_listings
                                                               |
                                                               v
                                    Provider application worker (Chromium)
                                                               |
                                         applications / application_attempts
                                                               |
                                                               v
                         house-screenshots volume  <── screenshots and evidence
```

The frontend must use a backend API for application state and screenshots. A
browser must not mount or query the SQLite database directly.

## Current baseline

`query/` provides four independent worker commands:

| Worker | Command | Input | Durable output |
| --- | --- | --- | --- |
| Yahoo poller | `python -m query_service` | Yahoo IMAP | `seen_emails` |
| Listing extractor | `python -m query_service.processor` | `seen_emails` | `listings` |
| Stekkies resolver | `python -m query_service.resolver` | `listings` | `resolved_listings` |
| Provider review worker | `python -m query_service.application_worker` | `resolved_listings` | `applications` + screenshot |

All pipeline tables are in `pipeline.sqlite3`. `PipelineDatabase` keeps SQL in one
place, opens a short-lived connection per operation, turns on WAL mode, and
uses a busy timeout plus retry for brief write contention. The extractor and
resolver already make their queue handoff atomic: write the downstream record
and mark the source record read in the same transaction. The provider review
worker atomically claims a resolved listing with a finite lease, captures a
screenshot, and stops at `awaiting_review`; it does not fill or submit forms.

## Docker topology

Use one named volume for SQLite and one distinct named volume for screenshots:

```text
house-query-data  -> /app/data       (read/write in every worker)
house-screenshots -> /app/screenshots (read/write only in the application worker)
```

Every worker receives the same mounted, read-only `values.yaml` (or equivalent
Docker secret) and must configure its database as:

```yaml
database: "/app/data/pipeline.sqlite3"
```

The application worker additionally mounts ignored `accounts.yaml`. Copy
`query/accounts.yaml-template` and include only provider accounts you own. The
credentials never leave Playwright and are never sent to Gemini.

All four services share the `house-query-service` image and differ only by
command. Split an application worker into a separate image only when its
dependencies or release cadence genuinely diverge; image count is not a
service boundary.

`docker-compose.yml` defines the four services individually. It uses local
named `house-query-data` and `house-screenshots` volumes, read-only
`values.yaml`, restart policies, `--init`, and memory/shared-memory limits for
the Chromium workers. Start all four with `docker compose up -d --build`.

Run exactly one replica of each worker initially. Use Docker restart policies
and `--init` (or their Compose equivalents) so SIGINT/SIGTERM reaches Python
and Chromium. Containers must not rely on writable state in their own
filesystems.

## SQLite rules

Multiple containers can safely access the same SQLite database **when all of
them are on this VPS and the named volume is backed by its local filesystem**.
WAL supports concurrent readers and one writer. It does not allow concurrent
write transactions; that is acceptable here because each database write is
small and the existing retry policy handles temporary lock contention.

These restrictions are mandatory:

- Do not mount `house-query-data` from NFS, SMB/CIFS, object-storage FUSE, or
  another network filesystem.
- Do not run these SQLite-backed containers on different Docker hosts.
- Mount `/app/data` read/write, not the database file alone. SQLite needs the
  adjacent `pipeline.sqlite3-wal` and `pipeline.sqlite3-shm` files.
- Do not hold a database transaction while calling IMAP or driving Chromium.
  Claim/read work, close the transaction, perform network/browser work, then
  open a short transaction to record the result.
- Back up with SQLite's online backup mechanism, or stop all writers first.
  Never copy just `pipeline.sqlite3` while it is live.

## Provider-application worker

The fourth worker consumes `resolved_listings`. Its current responsibility is
to open the provider URL, save a screenshot, and record `awaiting_review`.
It deliberately does not complete or submit an application.

Add application-domain tables and database-layer methods in
`query_service/database.py` rather than writing SQL inside browser code. A
minimal durable model needs:

| Field / concept | Purpose |
| --- | --- |
| source identity | A unique reference to the resolved listing being applied to. |
| provider URL | The exact destination visited. |
| status | `pending`, `processing`, `awaiting_review`, `submitted`, or `failed`. |
| lease expiry | Allows recovery when a worker dies after claiming work. |
| attempt count and error | Makes retry behaviour visible and bounded. |
| screenshot key/path | Refers to a file in `/app/screenshots`, never to a container-local path. |
| timestamps | Records creation, claim, completion, and review/submission time. |

The worker must use this sequence:

1. Atomically create or claim one application row from an unread
   `resolved_listings` item, set a finite lease, and increment its attempt
   count. This transaction must also acknowledge the resolved-listing queue
   item only when the application row was made durable.
2. Close the SQLite connection.
3. Use Chromium to visit the provider site without filling or submitting a form.
4. Save the screenshot atomically: write a temporary file under the screenshot
   volume, then rename it to its final stable name.
5. In a short transaction, record the screenshot key and final status. On a
   browser failure, record a retryable `failed` state with diagnostic context;
   do not silently discard the item.

The worker stops at `awaiting_review` before an irreversible provider
submission. It uses a predefined provider Playwright flow when one is
registered. Otherwise, generic Dutch/English matching runs first and only a
failure can trigger Gemini 2.5 Flash. Gemini receives the failure reason and
redacted visible controls, and can request one validated step at a time (up to
three per URL). A Gemini-requested provider login uses an exact-host credential
from ignored `accounts.yaml`; a missing entry is stored in `applications.error`.
The frontend/API can expose the screenshot and an explicit human approval
action. Automatic submission can be added only as a separately reviewed
capability with provider-specific safeguards.

## Scaling and recovery

With one worker per stage, the current read-then-ack queue methods are
sufficient. Before increasing any worker above one replica, implement an
atomic claim (`pending -> processing`) with a lease and a recovery query for
expired leases. Without it, two replicas can both process the same row.

At-least-once processing is intentional. Durable uniqueness constraints and
atomic handoffs must make a restarted worker safe to repeat. Browser activity
is inherently not transactionally reversible, so every provider interaction
needs an idempotency strategy where the provider supports one, plus a visible
reviewable record when it does not.

## Implementation order

1. Keep the existing poller, extractor, and resolver commands unchanged and
   verify that they all use `/app/data/pipeline.sqlite3`.
2. Add application tables, migrations, and `PipelineDatabase` methods with
   unit tests for deduplication, atomic handoff, claim leases, expired-lease
   recovery, and failure recording.
3. Add the provider application worker as a new command and container service.
   Its browser logic must depend on database-layer methods, not direct SQL.
4. Add the screenshots volume and ensure screenshots survive container
   recreation.
5. Add a backend API and frontend that show status, errors, and screenshots.
6. Add container orchestration (normally a Compose file) with all workers on
   the same local Docker host, named volumes, read-only configuration mounts,
   restart policies, and resource limits for Chromium workers.
7. Exercise crash recovery by killing each worker during processing and proving
   that no durable queue item or screenshot reference is lost.

## Acceptance criteria

- Restarting any one worker does not erase pipeline state, screenshots, or
  credentials.
- Poller, extractor, resolver, and application worker can run together without
  unhandled `database is locked` failures.
- A Chromium worker never keeps a SQLite transaction open during navigation.
- The same resolved listing cannot produce two active application attempts.
- Screenshots remain available after the application-worker container is
  removed and recreated.
- The frontend has no direct filesystem or SQLite-volume access.

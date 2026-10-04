# Small OVHcloud Docker VPS

This repository deploys to an already-purchased OVHcloud VPS. In OVHcloud Manager, go to **Bare Metal Cloud → Virtual Private Servers → your VPS** and copy its public IPv4 into `ansible/inventory.ini`. Infrastructure provisioning is intentionally outside this repository.

To install Docker, edit `ansible/inventory.ini` and load the same private key whose public half is installed on the VPS:

```bash
ssh-add ~/.ssh/id_ed25519
cd ansible
./run-playbook.sh
```

The runner deliberately uses your existing SSH agent (`SSH_AUTH_SOCK`), so no private-key path or password is stored in the repository. It checks that an agent has a loaded key and relies on the configured VPS account's passwordless `sudo`, so it does not prompt for either SSH or elevation passwords. Before the first Ansible run, confirm `ssh YOUR_ANSIBLE_USER@YOUR_VPS_IPV4` succeeds with the loaded key; this also records the server host key locally. The script creates or reuses `ansible/.venv` and installs the pinned Ansible requirement only inside that project virtual environment—never with `apt` on the control machine. The playbook always refreshes APT metadata, upgrades packages, installs `docker.io`, enables Docker, and adds the SSH user to the `docker` group.

To publish the basic Nginx health page at `http://YOUR_VPS_IPV4/`, run the separate Nginx deployment:

```bash
cd ansible
./deploy-nginx-landing.sh
```

It installs and starts Nginx, enables the repository's site configuration, and writes only `/srv/house-bot/frontend/index.html`. The Docker playbook does not deploy this page; later frontend deployments can replace that file independently.

## Yahoo Stekkies query service

`query/` has four single-threaded Python workers sharing one SQLite file: `data/pipeline.sqlite3`. The database has only five tables: `emails`, `stekkies_links`, `provider_listings`, `screenshots`, and derived `error_summaries`. The Yahoo poller only inserts unique raw emails as `pending`. Each following worker atomically claims a `pending` source row, sets it to `processing`, writes the deduplicated next-table record, then sets its own source row to `processed`; on an exception it records a message and sets that row to terminal `error`. No normal worker picks `error` rows up again. Every source entity therefore owns `status`, `processed_at`, and `processing_error`, while the optional summarizer rebuilds grouped observability counts every two hours. All SQL lives in `query_service/database.py`; each operation opens a short-lived SQLite connection, enables WAL, uses a busy timeout, and retries brief lock contention. See [the pipeline/database design](docs/docker-pipeline-migration.md) for the exact model and migration behaviour.

Yahoo requires an app password for third-party IMAP access. Copy `query/values.yaml-template` to `query/values.yaml` and enter your Yahoo email plus an app password—not your normal Yahoo password. Set `database` if you need a path other than `data/pipeline.sqlite3`. Install runtime dependencies with `python3 -m pip install -r requirements.txt`; for tests, install `python3 -m pip install -r requirements-test.txt`. Start the poller with `python3 -m query_service` and the one listing processor with `python3 -m query_service.processor`; use `--once` for a single cycle. The default is one poll every five minutes (12/hour); Yahoo does not publish a fixed IMAP polling limit, so the service enforces five minutes as a conservative minimum. Press `Ctrl+C` to stop either continuous service cleanly. Run all offline groups with `python3 -m pytest -m "not integration" -vv` from `query/`, or select `unit`, `database`, `service`, or `resolver` with `-m`. Pytest keeps expected logs captured unless a test fails; add `--log-cli-level=INFO` when you want live timestamped service logs.

For convenience, use `./run-poller.sh` and `./run-processor.sh` from `query/`. Run `./run-error-summary.sh` from `query/` on a two-hour host schedule to rebuild observability summaries. `./test.sh` runs the offline suite and replays saved emails in `data/test.sqlite3`: it clears only links/provider listings/screenshots, resets the saved emails to pending, runs the processor, and prints listing URL logs. It never deletes email rows in `data/test.sqlite3`.

`./run-resolver.sh` is the third process. It uses local headless Chromium through Playwright, not Browserbase. Paste a logged-in Stekkies browser Cookie header into the Git-ignored `values.yaml`; Playwright loads those cookies into its browser context, opens the exact emailed **View match** link, and follows the rendered listing action (currently **Go to listing**) to create or reuse the provider-listing row. That write and marking the source Stekkies link `processed` are one transaction. Failed navigations/clicks log the failure step, browser URLs, action URL, page title, and traceback, then mark that exact link `error`; it is visible for investigation and is never automatically retried. The resolver waits a random 20–35 seconds between attempts. Install the browser once with `python3 -m playwright install chromium`. Cookies expire; replace the value after logging into Stekkies again.

### Docker: four independent workers

Set `database: "/app/data/pipeline.sqlite3"` in the ignored `query/values.yaml`. The Compose configuration runs four separate containers—Yahoo poller, extractor, Stekkies resolver, and provider-review worker. They communicate only through the local named SQLite volume. The provider-review worker saves screenshots to its separate named volume and stops at human review; it never fills or submits an application.

```bash
docker compose up -d --build
```

Each service is a single Python worker process; no container starts or supervises another worker process. The database layer creates the configured SQLite file and all pipeline tables if absent. Keep the named volumes on this one VPS only: SQLite WAL requires a local filesystem and the whole `/app/data` directory must be mounted so its WAL and shared-memory sidecar files persist.

For a controlled live-flow demonstration, run `RUN_LIVE_YAHOO_TEST=1 python3 -m pytest -m integration -vv` from `query/`. This group is skipped unless explicitly selected. It reads one current Stekkies email from Yahoo without changing mailbox state, copies it to `query/data/test.sqlite3`, marks only the copied email row pending, runs the processor, and asserts that extracted URLs are logged and queued in that same test-only database. It never changes the configured production database.

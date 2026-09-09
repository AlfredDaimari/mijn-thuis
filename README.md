# Small OVHcloud Docker VM

`terraform/` defines one OVHcloud VPS-1: the low-cost VPS option with 2 vCores, 4 GB RAM, and 40 GB NVMe storage. It is an OVHcloud VPS order, not a Public Cloud instance. Standard public networking is part of the VPS product, so no Public Cloud project or separate network is configured.

Copy `terraform/terraform.tfvars-example` to `terraform/terraform.tfvars`, fill in the current VPS-1 plan code, datacenter, Ubuntu image ID, and absolute path to your public `.pub` SSH key. Configure OVH provider credentials using its supported environment variables and ensure the account has a default payment method. Terraform pre-installs the public key on the VPS. After provisioning, copy the assigned public IPv4 from the OVHcloud Control Panel into `ansible/inventory.ini`.

The actual `terraform.tfvars` is intentionally Git-ignored. No Terraform commands have been run by this project setup.

To install Docker, edit `ansible/inventory.ini`, make the runner executable once with `chmod +x ansible/run-playbook.sh`, and run it from the `ansible` directory. The script installs Ansible on a Debian/Ubuntu control machine if needed. The playbook always refreshes APT metadata, upgrades packages, installs `docker.io`, enables Docker, and adds the SSH user to the `docker` group.

## Yahoo Stekkies query service

`query/` has three single-threaded Python processes and three operational SQLite databases. The Yahoo poller reads only messages sent from `stekkies.com` and writes the full email to `data/staging.sqlite3` with `is_read = 0`. The listing processor reads that staging queue, extracts HTTP(S) listing links and HTML anchor titles, writes each result to `data/listings.sqlite3` with its own `is_read = 0`, then marks the source email as read. The resolver reads that listing queue and writes the external provider URL to `data/resolved.sqlite3`, also unread. A SHA-256 signature prevents duplicate email queue entries, while `(source_signature, url)` prevents duplicate listing entries. All SQL now lives in `query_service/database.py`; services call its database methods and never share a SQLite connection. The database layer opens a connection per operation, enables WAL mode, uses a busy timeout and retries lock contention, so the poller can write while the processor reads. Keep one consumer of each queue, because reading and marking an item are intentionally separate operations. All processes write timestamped logs without passwords or email bodies. The processor logs an error with the email subject when a Stekkies email has no extractable listing links.

Yahoo requires an app password for third-party IMAP access. Copy `query/values.yaml-template` to `query/values.yaml` and enter your Yahoo email plus an app password—not your normal Yahoo password. Configure the database paths if you need paths other than the default files in `data/`. Install runtime dependencies with `python3 -m pip install -r requirements.txt`; for tests, install `python3 -m pip install -r requirements-test.txt`. Start the poller with `python3 -m query_service` and the one listing processor with `python3 -m query_service.processor`; use `--once` for a single cycle. The default is one poll every five minutes (12/hour); Yahoo does not publish a fixed IMAP polling limit, so the service enforces five minutes as a conservative minimum. Press `Ctrl+C` to stop either continuous service cleanly. Run all offline groups with `python3 -m pytest -m "not integration" -vv` from `query/`, or select `unit`, `database`, `service`, or `resolver` with `-m`. Pytest keeps expected logs captured unless a test fails; add `--log-cli-level=INFO` when you want live timestamped service logs.

For convenience, use `./run-poller.sh` and `./run-processor.sh` from `query/`. `./test.sh` runs the offline suite and replays the saved email in `data/test.sqlite3`: it clears only `data/test-listings.sqlite3`, resets the copied email to unread, runs the processor, and prints its listing URL logs. It never deletes the email row in `data/test.sqlite3`.

`./run-resolver.sh` is the third process. It uses local headless Chromium through Playwright, not Browserbase. Paste a logged-in Stekkies browser Cookie header into the Git-ignored `values.yaml`; Playwright loads those cookies into its browser context, opens the exact emailed **View match** link, and follows the rendered listing action (currently **Go to listing**) to write the external provider URL to `data/resolved.sqlite3` with `is_read = 0`. It marks the source listing read only after that write succeeds. Failed navigations/clicks log the failure step, browser URLs, action URL, page title, and traceback, then stay unread for a later retry. The resolver waits a random 20–35 seconds between attempts. Install the browser once with `python3 -m playwright install chromium`. Cookies expire; replace the value after logging into Stekkies again.

### Docker

Build the image with `docker build -t house-query-service ./query`, then create its persistent database volume once with `docker volume create house-query-data`. The image does not contain `values.yaml` or `data/`. Mount both at runtime; the configured database paths default to `/app/data/...` because the container runs in `/app`.

```bash
docker run --rm --init \
  --mount type=bind,src="$(pwd)/query/values.yaml",dst=/app/values.yaml,readonly \
  --mount type=volume,src=house-query-data,dst=/app/data \
  house-query-service
```

That starts the Yahoo poller. Run the other single-process consumers with the same two mounts and an overriding command: `house-query-service python -m query_service.processor` or `house-query-service python -m query_service.resolver`. The database layer creates each configured SQLite file if it is absent. `--init` forwards signals cleanly to Python and Chromium.

For a controlled live-flow demonstration, run `RUN_LIVE_YAHOO_TEST=1 python3 -m pytest -m integration -vv` from `query/`. This group is skipped unless explicitly selected. It reads one current Stekkies email from Yahoo without changing mailbox state, copies it to `query/data/test.sqlite3`, marks only the copied row unread, runs the processor, and asserts that extracted URLs are logged and queued in its test-only listings database. It never changes the configured production databases.

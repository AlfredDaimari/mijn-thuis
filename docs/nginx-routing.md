# Nginx routing design

The Ansible-installed Nginx is the public edge proxy on the VPS. It forwards
every request to the Docker `frontend` service at `127.0.0.1:8080`. That
container runs the dashboard Nginx, which serves the React build, checks cookie
sessions before serving screenshots, and proxies API requests to the private API
container. The public deployment will add HTTPS at the edge as specified in
[`frontend.md`](frontend.md).

## Routes

| Route | Owner | Purpose |
| --- | --- | --- |
| `/` | Docker frontend Nginx | React dashboard build. |
| `/api/` | Docker frontend Nginx → API container | Authenticated, read-only API; it is never published on a host port. |
| `/screenshots/<key>.png` | Docker frontend Nginx | Serves the exact screenshot file written by the application worker. |

## Screenshot ownership

The application-worker and Docker frontend containers mount the same host
directory, `/srv/house-bot/screenshots`. The worker writes it at
`/app/screenshots`; the frontend Nginx serves it read-only at `/screenshots/`.
A screenshot key saved in SQLite therefore maps directly to a public path such
as `/screenshots/<key>.png`, with no copy step or container-local state.

The directory is persistent across container recreation. Directory listing is
disabled and responses are marked `Cache-Control: private, no-store`. The
Docker frontend Nginx reads `frontend_auth` from the ignored `values.yaml` at
startup to generate an in-container password file; the API independently
checks the same credentials. The API returns only stored Nginx-relative
screenshot paths from `screenshots`.

## Deployment prerequisite

The Compose default expects `/srv/house-bot/screenshots` and binds the Docker
frontend to `127.0.0.1:8080`. For a non-VPS local run, set
`HOUSE_SCREENSHOTS_DIR` to an existing absolute host directory and optionally
set `HOUSE_FRONTEND_PORT` before starting Compose. Do not point Nginx at
Docker's internal named-volume path.

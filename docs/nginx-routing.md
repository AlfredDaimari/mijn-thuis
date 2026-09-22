# Nginx routing design

The Ansible playbook installs and enables Nginx on the VPS. It owns the public
HTTP port; Docker workers do not publish their own HTTP ports. The current
landing page is HTTP-only while the server is being brought up. The planned
dashboard deployment changes the public origin to HTTPS with HTTP redirected
to HTTPS, as specified in [`frontend.md`](frontend.md).

## Routes

| Route | Owner | Purpose |
| --- | --- | --- |
| `/` | static frontend directory | Future web pages from `/srv/house-bot/frontend`. No frontend code is installed yet. |
| `/api/` | future Python backend at `127.0.0.1:8000` | Reserved reverse-proxy route for the service that will read the pipeline database. No backend code is installed yet. |
| `/screenshots/<key>.png` | Nginx static alias | Serves the exact screenshot file written by the application worker. |

## Screenshot ownership

The application-worker container mounts the host directory
`/srv/house-bot/screenshots` at `/app/screenshots`. Nginx aliases that same
host directory at `/screenshots/`. A screenshot key saved in SQLite therefore
maps directly to a public path such as `/screenshots/<key>.png`, with no copy
step and no container-local state.

The directory is persistent across container recreation. Directory listing is
disabled and responses are marked `Cache-Control: private, no-store`. Before
the dashboard is exposed, the same Nginx Basic Auth realm must protect `/`,
`/api/`, and `/screenshots/`; HTTPS must be live first. The API should return
only stored Nginx-relative screenshot paths from `application_screenshots`.

## Deployment prerequisite

The Compose default expects `/srv/house-bot/screenshots`. For a non-VPS local
run, set `HOUSE_SCREENSHOTS_DIR` to an existing absolute host directory before
starting Compose. Do not point Nginx at Docker's internal named-volume path.

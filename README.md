# Plumbus

Plumbus is a private Plex-powered cinema catalogue and playback gateway designed for VRChat-compatible video players and ordinary HTTPS clients. It is a standalone application: Plex remains the media source, while Plumbus handles Discord identity, invite-only registration, catalogue indexing, role-based administration, temporary playback URLs, artwork proxying, playback history, audit logging, and server-side streaming without exposing Plex credentials.

## Features

- Discord OAuth2 (`identify`) as the only account authentication method.
- Strict invite-only registration. Normal Discord login never creates an unknown account.
- `SuperAdmin`, `Admin`, `Support`, and `Member` role hierarchy with server-side authorization.
- Initial SuperAdmin bootstrap locked to one configured Discord user ID.
- Cryptographically random invite/session/playback tokens; sensitive reusable tokens are stored hashed.
- Encrypted database-backed Plex token configuration with environment-variable bootstrap/fallback.
- Plex server test, library discovery, per-library visibility, full/incremental/single-library/single-movie scans.
- Celery + Redis background scanning and Celery Beat periodic synchronization.
- Optional authenticated Plex webhook ingestion for targeted refreshes.
- Search and filters for title/people/tags/year/genre/library/collection/content rating/resolution.
- Local metadata overrides without modifying Plex metadata.
- Poster and backdrop proxying; Plex authentication is never placed in browser image URLs.
- Temporary playback URLs intended to work with clients that cannot supply cookies or custom headers.
- Direct media proxy with HTTP Range support and chunked transfer for large files.
- Optional Plex HLS transcoding through an opaque server-side playlist/segment proxy.
- Playback history and Continue Watching data.
- Audit logs, sanitized Support views, account suspension/bans, and final-SuperAdmin protections.
- Responsive React frontend and full-screen cinema/admin layouts.
- Docker Compose production stack with PostgreSQL, Redis, backend, worker, beat, frontend, and Nginx.

## Architecture

```text
                           +----------------------+
Discord OAuth ----------> |      Plumbus API     |
                           | FastAPI / SQLAlchemy |
                           +----------+-----------+
                                      |
                  +-------------------+-------------------+
                  |                   |                   |
                  v                   v                   v
             PostgreSQL            Redis              Plex Server
                  ^                   ^                   ^
                  |                   |                   |
                  +------- Celery Worker / Beat ----------+
                                      |
                                      v
VRChat / Browser ---> Nginx ---> /stream/{temporary-token}
                         |
                         +------> React static frontend
```

Only the edge Nginx container is published by the default Compose file. PostgreSQL, Redis, FastAPI, Celery and the frontend container live on an internal Docker network.

## Repository layout

```text
backend/
  app/
    api/
    core/
    db/
    models/
    security/
    services/
      invitations/
      playback/
      plex/
    workers/
    tests/
  alembic/
  Dockerfile
  docker-entrypoint.sh
frontend/
  src/
  Dockerfile
  nginx.conf
nginx/
  default.conf
docker-compose.yml
docker-compose.prod.yml
docker-compose.dev.yml
.env.example
```

## Requirements

For the recommended deployment you need Docker Engine with the Compose v2 plugin, a Plex Media Server reachable from the backend/worker containers, a Discord application, a public HTTPS hostname for production, and enough disk space for PostgreSQL/Redis volumes. Plumbus does not copy your movie library into its database; it stores catalogue metadata and proxies playback from Plex.

## Production quick start

The default `docker-compose.yml` is production-safe. `docker-compose.prod.yml` is only an optional override that explicitly forces production application settings.

```bash
git clone <your-private-repository-url> Plumbus
cd Plumbus
cp .env.example .env
```

Edit `.env` before starting. At minimum replace the database password, `SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, Discord credentials, public application URL/redirect URL, initial SuperAdmin Discord ID, and Plex configuration. Never reuse the example secret strings.

Generate strong secrets, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start the stack:

```bash
docker compose up -d --build
```

The backend waits for PostgreSQL and Redis, automatically runs `alembic upgrade head`, then starts Gunicorn. Nginx is published on `HTTP_PORT` (8080 by default). For an explicit production override:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Useful checks:

```bash
docker compose ps
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f beat
curl http://127.0.0.1:${HTTP_PORT:-8080}/health
```

Production deployments should terminate TLS before or at the Plumbus edge. `APP_URL`, `DISCORD_REDIRECT_URI`, `TRUSTED_HOSTS`, and `CORS_ORIGINS` must use the real HTTPS hostname.

## Development setup

Use the explicit development override for source mounts, Uvicorn reload, and the Vite development server:

```bash
cp .env.example .env
# Change APP_ENV=development, APP_URL/redirect URL to localhost,
# COOKIE_SECURE=false, and optionally MOCK_PLEX=true.
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

`MOCK_PLEX=true` supplies development fixtures such as Interstellar, The Matrix, Blade Runner 2049, Dune and The Dark Knight. Production configuration refuses to start with mock Plex enabled.

## Environment configuration

See `.env.example` for the canonical list. Important groups are:

- Application: `APP_ENV`, `APP_URL`, `HTTP_PORT`.
- Database: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`.
- Redis: `REDIS_URL`.
- Discord: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `INITIAL_SUPERADMIN_DISCORD_ID`.
- Plex: `PLEX_URL`, `PLEX_TOKEN`, `PLEX_SCAN_INTERVAL_MINUTES`, optional `PLEX_WEBHOOK_SECRET`.
- Security: `SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, `COOKIE_SECURE`, `TRUSTED_HOSTS`, `CORS_ORIGINS`.
- Playback: `PLAYBACK_TOKEN_LIFETIME_MINUTES`, `PREFERRED_VIDEO_CODEC`, `PREFERRED_RESOLUTION`, `MAX_STREAM_BITRATE_KBPS`, `ALLOW_PLEX_TRANSCODING`.
- Runtime tuning: `WEB_CONCURRENCY`, `GUNICORN_TIMEOUT`, `CELERY_CONCURRENCY`, `CELERY_LOG_LEVEL`.

Keep `TOKEN_ENCRYPTION_KEY` stable after saving Plex configuration in the database. Changing it without first rotating/re-saving encrypted secrets makes those stored values unreadable.

## Discord OAuth setup

Create an application in the Discord Developer Portal and configure an OAuth2 redirect URI exactly matching:

```text
https://your-host.example/api/auth/discord/callback
```

Set the matching values in `.env`:

```text
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
DISCORD_REDIRECT_URI=https://your-host.example/api/auth/discord/callback
```

Plumbus requests only the `identify` scope. It stores the Discord user ID, username, global/display name, avatar reference, account role/status, registration date and last login. It never receives or stores a Discord password.

### Existing user login

`/login` starts the normal OAuth flow. After Discord returns, Plumbus looks up the Discord ID. If no account exists, the flow ends with an invite-required message and no user row is created.

### New user registration

A new account must begin at `/invite/{token}`. The invite is validated before Discord OAuth starts and again while redemption is locked in the database. The OAuth identity is then permanently associated with the newly created account. Reusing normal login later does not require another invite.

## Initial SuperAdmin

Set:

```text
INITIAL_SUPERADMIN_DISCORD_ID=your_numeric_discord_user_id
```

For a brand-new install with no SuperAdmin, use the initial bootstrap action presented by the application. The OAuth callback only grants SuperAdmin when Discord returns the exact configured ID. A random first visitor cannot claim SuperAdmin merely because the user table is empty.

After initial setup, ordinary invites follow normal role ceilings: SuperAdmin may create Admin invites; Admin may create Support/Member invites; Support cannot create invitations by default. Admin cannot promote a user to SuperAdmin or remove the final SuperAdmin.

## Plex setup

You can bootstrap Plex with environment variables:

```text
PLEX_URL=http://host.docker.internal:32400
PLEX_TOKEN=...
```

On Linux Compose, the backend/worker/beat containers map `host.docker.internal` to the Docker host. If Plex is on another machine, use its LAN/VPN-reachable address instead.

A SuperAdmin may later update Plex from the administration UI/API. The supplied settings are connection-tested before being saved, and the token is encrypted with `TOKEN_ENCRYPTION_KEY`. API responses report only whether a token is configured; they never return its value.

After connecting Plex:

1. Discover libraries.
2. Enable only the libraries intended for Plumbus.
3. Choose whether each library is visible to Members or admin-only.
4. Run a scan.

If the configured Plex machine identifier changes, old library mappings are disabled until they are rediscovered/re-enabled, preventing accidentally exposing numeric library keys from a different server.

## Plex scanning

Available modes are:

- Full Scan: queues every enabled library and reconciles the complete local catalogue.
- Incremental Scan: used by the periodic scheduler and available manually; it refreshes enabled libraries in the background.
- Single Library Scan: refreshes one enabled library.
- Single Movie Refresh: refreshes only the selected movie and does not delete unrelated catalogue entries.

Jobs record mode, status, start/end times, errors, scanned/added/updated/removed counters, and requester. Started/completed/failed lifecycle events are written to audit logs.

Celery Beat uses `PLEX_SCAN_INTERVAL_MINUTES` for automatic synchronization. Large scans do not block HTTP request workers.

## Optional Plex webhooks

Set a long random `PLEX_WEBHOOK_SECRET`, then configure Plex to call:

```text
https://your-host.example/api/webhooks/plex?secret=YOUR_RANDOM_SECRET
```

The endpoint accepts Plex's multipart `payload`, compares the secret using a constant-time comparison, and queues a targeted movie refresh when it can map the incoming `ratingKey`. Library events fall back to enabled-library refreshes. If no webhook secret is configured, the endpoint is disabled.

Treat the webhook URL as a secret because it contains the webhook credential.

## Catalogue and search

Authenticated users can browse enabled libraries and search indexed title/person/tag metadata. Filters include genre, year, resolution, library, collection and content rating, with recently added, recently updated and alphabetical ordering. Members never receive the stored Plex filesystem path.

Admins can store local title/summary/tagline/year/content-rating overrides. These are applied by Plumbus only and do not modify Plex metadata.

## Playback architecture

Playback starts through:

```text
POST /api/playback/movies/{movie_id}
```

The response contains a random temporary application URL and expiration time. The raw playback token is not stored in the database; only its hash is stored. The URL can be opened with a normal HTTPS GET and therefore does not require a cookie, JavaScript, custom Authorization header, or `X-Plex-Token`.

### Direct playback

For a selected direct-play/direct-stream candidate, Plumbus serves:

```text
https://your-host.example/stream/TEMPORARY_TOKEN
```

The backend validates token expiry/revocation and the owning account's status, resolves the Plex part server-side, forwards `Range`, and streams chunks without buffering the whole movie in application memory. Nginx disables buffering/temp-file use for `/stream/` and permits long-running transfers.

### Plex transcoding

If a media version is classified as `Transcode Required` and transcoding is enabled, the temporary URL points to an internal HLS master route. Plumbus requests Plex's universal transcoder server-side and rewrites every nested playlist/key/segment URI to another opaque Plumbus URL. Upstream resource URLs are encrypted and validated to remain on the configured Plex host before they are fetched.

This means a browser/VRChat client does not receive the Plex token even when Plex embeds authentication in its own HLS URLs.

Transcoding is intentionally disabled by default. Enable it only when the Plex host has enough CPU/GPU resources and test the chosen output with the target VRChat player. Codec support differs across user systems; Plumbus reports media information rather than assuming HEVC/AV1 compatibility.

## Playback history

The application tracks playback start, last position, completion state and last watched time. Use:

```text
POST /api/history/start
POST /api/history/progress
POST /api/history/complete
GET  /api/history/continue-watching
```

Creating a playback URL also initializes the user's history row. A VRChat-world integration can call progress endpoints if the world/backend bridge is able to report playback position.

## Roles

`SuperAdmin > Admin > Support > Member` is enforced by backend authorization checks, not frontend visibility.

SuperAdmin can manage users/roles/security-sensitive configuration, Plex settings, administrators, invitations and system settings. Admin can manage Members/Support, invites, scans and local metadata but cannot create/remove SuperAdmin. Support can inspect basic account/invite/Plex status and receives sanitized logs without secret/token/IP detail. Member receives catalogue/profile/playback functionality only.

## Health checks

Public basic health:

```text
GET /health
```

Component endpoints exist for database, Redis and Plex checks. Do not expose extra infrastructure diagnostics through a public reverse proxy unless the response is suitably sanitized for your environment.

Docker Compose also defines healthchecks and dependency ordering so Nginx does not start serving against an unhealthy backend/frontend.

## Security notes

Plumbus uses OAuth state expiry/one-time consumption, HttpOnly sessions, Secure cookies in production, SameSite policy, server-side RBAC, origin/CSRF checks for state-changing API requests, Redis throttling for sensitive unauthenticated flows, invite expiry/max-use/revocation checks, hashed invite/session/playback tokens, protected final-SuperAdmin operations, encrypted persisted Plex credentials, sanitized Support logs, TrustedHost/CORS controls, and Nginx security headers/CSP.

The frontend is never a security boundary. Any UI action that changes protected state is checked again on the backend.

Do not commit `.env`, database dumps, Plex tokens, Discord client secrets, session secrets, encryption keys, webhook secrets, or generated invitation/playback URLs.

## Nginx and HTTPS

The included Nginx is the application edge inside Compose and listens on HTTP. Put it behind your TLS terminator/reverse proxy, or adapt the deployment to mount certificates. Preserve the original Host and `X-Forwarded-Proto` headers. `/stream/` must not be buffered by an outer proxy and should allow Range requests and long read timeouts.

If Cloudflare or another CDN is in front, review its maximum request duration, cache rules and large-video policies before relying on it for media proxy traffic.

## Backups

Back up PostgreSQL and your `.env`/secret configuration separately. Example database dump:

```bash
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > plumbus-$(date +%F).sql
```

Restore into a stopped/maintenance deployment after verifying the target schema/version. Redis contains queue/scheduler state and is not the source of truth for accounts/catalogue; PostgreSQL is the critical persistent database.

Protect backups because they contain Discord identifiers, audit data, playback history and encrypted secrets. The encryption key itself should be stored outside the database backup.

## Updating

Recommended update procedure:

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
```

The backend entrypoint runs pending Alembic migrations before starting Gunicorn. Always inspect release notes/migrations and take a PostgreSQL backup before production upgrades.

After updating:

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
curl -fsS http://127.0.0.1:${HTTP_PORT:-8080}/health
```

## Tests and validation

Backend:

```bash
cd backend
pip install -e '.[dev]'
alembic upgrade head
pytest -q
ruff check app
```

Frontend:

```bash
cd frontend
npm install
npm test
npm run build
```

Compose validation/build:

```bash
cp .env.example .env
# Replace required CHANGE_ME values first.
docker compose config
docker compose build
```

GitHub Actions mirrors backend tests/lint, frontend tests/build, Compose configuration validation and both Docker image builds.

## Troubleshooting

**Compose refuses to interpolate `POSTGRES_PASSWORD`:** set it in `.env`; the production Compose file deliberately requires it.

**Backend stays unhealthy:** inspect `docker compose logs backend`. Migration/config validation failures intentionally stop the API instead of serving a partially configured application.

**Discord says redirect mismatch:** the redirect in the Discord Developer Portal, `DISCORD_REDIRECT_URI`, scheme, hostname, port and path must match exactly.

**Initial SuperAdmin cannot bootstrap:** verify the numeric Discord ID in `INITIAL_SUPERADMIN_DISCORD_ID` and confirm no SuperAdmin already exists.

**Plex is disconnected:** test network reachability from the backend container, not only the Docker host. For host-local Plex, verify `host.docker.internal`; for a remote server, check firewall/routing and the token.

**Library scans never start:** check Redis, worker logs and that the library is enabled. The API commits scan jobs before dispatching them so a healthy worker should be able to resolve the job immediately.

**Seeking fails:** verify the Plex source supports byte ranges, no outer reverse proxy strips `Range`, and `/stream/` buffering remains disabled.

**Transcoding fails:** verify Plex permits transcoding for the account/server, inspect Plex transcoder logs, confirm the preferred resolution/bitrate is reasonable, and test direct playback separately.

**Invite rejected:** inspect its expiration, revocation and use-count status. The same invite is revalidated under a database lock during OAuth completion.

**Stored Plex token becomes unreadable:** restore the original `TOKEN_ENCRYPTION_KEY`, or re-enter the Plex token after intentionally rotating the key.

## License and media responsibility

Plumbus does not provide media. Operators are responsible for the Plex server, content permissions, network exposure and compliance requirements applicable to their deployment.

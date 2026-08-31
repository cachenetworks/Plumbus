# Plumbus

Plumbus is a private Plex-powered cinema catalogue and playback gateway designed for VRChat-compatible video players and normal HTTPS clients.

It keeps Plex credentials server-side, uses Discord OAuth for member identity, supports invite-only registration, indexes selected Plex libraries, and creates temporary playback URLs that can be used by clients such as VRChat AVPro.

## New: web-first setup

A fresh Plumbus deployment no longer expects the installer to manually find a Plex token or place Discord/Plex application credentials into `.env`.

The recommended install flow is now:

```bash
git clone <your-private-repository-url> Plumbus
cd Plumbus
chmod +x install.sh
./install.sh
```

`install.sh` generates the infrastructure secrets Plumbus needs, creates `.env`, builds the Docker stack, and starts it.

Open:

```text
http://YOUR_SERVER:8080/
```

A fresh installation automatically redirects to `/setup`.

Get the one-time setup claim code with:

```bash
docker compose logs backend | grep 'SETUP CODE' | tail -1
```

The setup wizard then walks through:

1. Claiming the fresh installation with the one-time Docker-log code.
2. Choosing the public Plumbus URL and site name.
3. Configuring Discord OAuth and the first SuperAdmin Discord user ID.
4. Signing into Plex using Plex's official PIN/device authentication flow.
5. Selecting a Plex Media Server from the Plex account.
6. Selecting the best reported connection for that server.
7. Selecting the movie libraries Plumbus may index.
8. Choosing playback codec/resolution/bitrate/transcoding defaults.
9. Running a final readiness check.
10. Signing into Discord as the configured first SuperAdmin.

Once setup is completed, the unauthenticated setup wizard is locked.

## Plex sign-in

Plumbus uses Plex's device/PIN authentication instead of asking users to manually extract an `X-Plex-Token`.

The integration generates an Ed25519 device key, sends the public key to Plex during the PIN flow, stores the private key encrypted, obtains the Plex account token, and refreshes the account token automatically.

After Plex sign-in, Plumbus asks Plex for the account's available Media Servers. Plex returns server-specific access tokens and reported connection URLs. Plumbus prefers local, non-Relay, HTTPS connections when presenting choices, but the installer can choose whichever connection is reachable from the Docker host.

The selected PMS access token is encrypted with `TOKEN_ENCRYPTION_KEY` and is never returned to the browser.

## What still belongs in `.env`

Only infrastructure/bootstrap settings belong outside the application UI:

- PostgreSQL database credentials.
- Redis connection URL.
- `SESSION_SECRET`.
- `TOKEN_ENCRYPTION_KEY`.
- Docker port/runtime tuning.
- Scan cadence and playback-token lifetime defaults.

The included `install.sh` generates the important secrets automatically.

Discord Client ID/secret, owner Discord ID, public site URL, Plex account/server, Plex libraries and playback settings are stored through the web setup flow.

Keep `TOKEN_ENCRYPTION_KEY` stable. Changing it after integrations have been saved makes encrypted credentials unreadable.

## Architecture

```text
Browser / VRChat
       |
       v
     Nginx
       |
       +----> React frontend
       |
       +----> FastAPI
                |
        +-------+--------+
        |       |        |
     Postgres  Redis    Plex
                |
           Celery/Beat
```

The default Compose stack contains:

- PostgreSQL 17
- Redis
- FastAPI / Gunicorn
- Celery worker
- Celery Beat
- React static frontend
- Nginx edge proxy

Only Nginx is published externally by the default Compose configuration.

## Discord setup

During `/setup`, Plumbus shows the exact Discord OAuth redirect URI generated from the public site URL.

Create a Discord application and add that exact URI under OAuth2 redirects. Then paste the Client ID and Client Secret into the wizard and enter the numeric Discord ID that should become the first SuperAdmin.

Plumbus requests only the `identify` scope.

Normal Discord login never creates an unknown account. New users must register through a valid invitation URL.

## First SuperAdmin

After the wizard passes its readiness check, it redirects to the Discord bootstrap flow.

Only the exact Discord ID configured during setup can complete this flow. A random first visitor cannot become SuperAdmin simply because the users table is empty.

After the first SuperAdmin exists, normal role rules apply:

```text
SuperAdmin > Admin > Support > Member
```

## Plex libraries and scans

After Plex server selection, the wizard displays Plex libraries and requires at least one to be enabled.

Plumbus supports:

- Full scans
- Incremental scans
- Single-library scans
- Single-movie refreshes
- Periodic Celery Beat synchronization
- Optional authenticated Plex webhook refreshes

Scan jobs record status, timestamps, errors and item counters.

## Playback

Creating playback for a movie returns a temporary opaque URL.

Direct playback uses:

```text
/stream/<temporary-token>
```

Range requests and seeking are forwarded to Plex without exposing the Plex token.

If Plex transcoding is enabled and required, Plumbus proxies HLS playlists and segments through opaque application URLs so Plex credentials still do not appear in browser/VRChat URLs.

## Security

Plumbus includes:

- one-time first-run setup claim code
- encrypted Discord and Plex integration secrets
- hashed invitation/session/playback tokens
- Discord OAuth state validation and expiry
- HttpOnly user sessions
- invite expiry, max-use and revocation rules
- backend-enforced RBAC
- final-SuperAdmin protections
- dynamic post-setup host validation
- same-origin validation for state-changing API requests
- Redis rate limiting for setup/auth/invite flows
- sanitized audit/API/security logs
- Nginx CSP/security headers
- no Plex token exposure to frontend clients

The setup claim code is intentionally printed to backend Docker logs rather than displayed to an unauthenticated browser. This prevents someone who discovers a newly published URL first from taking ownership of the installation.

## Useful commands

```bash
# Start/update
docker compose up -d --build

# Status
docker compose ps

# First-run claim code
docker compose logs backend | grep 'SETUP CODE' | tail -1

# Backend logs
docker compose logs -f backend

# Worker logs
docker compose logs -f worker

# Health
curl http://127.0.0.1:8080/health
```

## Development

Use the development override:

```bash
cp .env.example .env
# Replace the CHANGE_ME infrastructure values.
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

For local fixture data you may use `MOCK_PLEX=true`; production rejects mock Plex mode.

## Backups

PostgreSQL is the source of truth for accounts, integration configuration, catalogue state, audit data and playback history.

Example backup:

```bash
docker compose exec -T postgres pg_dump -U cinema cinema > plumbus-$(date +%F).sql
```

Back up `.env` separately because `TOKEN_ENCRYPTION_KEY` is required to decrypt stored Discord/Plex credentials.

## Updating

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
```

The backend entrypoint applies Alembic migrations before starting the web service.

## Tests

Backend:

```bash
cd backend
pip install -e '.[dev]'
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

Compose:

```bash
docker compose config
docker compose build
```

## Important production notes

Use HTTPS for the public Plumbus URL. If TLS is terminated by another reverse proxy, preserve `Host` and `X-Forwarded-Proto`.

Do not buffer `/stream/` in an outer proxy, and allow long-running requests and byte ranges.

Do not commit `.env`, database dumps, Discord secrets, Plex tokens, setup codes, invitations or temporary playback URLs.

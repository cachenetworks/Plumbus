# Plumbus

Private Plex-powered VRChat cinema platform.

Plumbus is a standalone FastAPI + React application that indexes selected Plex libraries, authenticates exclusively with Discord OAuth2, enforces invite-only registration, and issues temporary tokenized playback URLs suitable for normal HTTPS clients such as VRChat AVPro.

## Stack

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL
- Celery + Redis for Plex indexing/background work
- React, TypeScript, Vite, Tailwind CSS
- Docker Compose + Nginx
- Discord OAuth2 (`identify`)
- Plex HTTP API / `plexapi`

## Security model

- Normal `/login` authenticates existing Discord IDs only.
- New accounts can only be created from a valid `/invite/{token}` flow.
- Invite and playback tokens are CSPRNG-generated and stored hashed.
- Plex tokens and Discord secrets stay server-side.
- RBAC is enforced by backend dependencies.
- `INITIAL_SUPERADMIN_DISCORD_ID` is the only Discord ID eligible for initial SuperAdmin assignment.

## Development

1. Copy `.env.example` to `.env`.
2. Fill Discord/Plex settings, or set `MOCK_PLEX=true` only for local development.
3. Run `docker compose up --build`.
4. Run `docker compose exec backend alembic upgrade head`.
5. Open `http://localhost:8080`.

## Initial SuperAdmin

Set `INITIAL_SUPERADMIN_DISCORD_ID` before deployment. That Discord ID still has to register through a valid invitation. Plumbus never promotes an arbitrary first user.

## Plex

Set `PLEX_URL` and `PLEX_TOKEN`. The backend talks to Plex server-side; tokens are never returned to browsers or stream clients. Admins can test Plex connectivity, choose libraries, and queue scans.

## Playback

`POST /api/playback/movies/{movie_id}` creates a temporary opaque playback URL. `GET /stream/{token}` validates it and proxies Plex with streaming chunks and HTTP byte-range support, without exposing `X-Plex-Token`.

## Tests

- Backend: `pytest`
- Frontend: `npm test`
- E2E: `npx playwright test`

## Production notes

Use `docker-compose.prod.yml`, HTTPS, strong generated secrets, PostgreSQL backups, and never commit `.env`. Production startup rejects mock Plex mode.

## Updating

Pull the release, rebuild images, run `alembic upgrade head`, and restart services. Review migrations before production upgrades.

## Troubleshooting

- OAuth callback mismatch: `DISCORD_REDIRECT_URI` must exactly match Discord Developer Portal.
- Plex disconnected: confirm the backend container can reach `PLEX_URL` and the token is valid.
- Seeking fails: verify upstream byte-range support and ensure Nginx buffering is disabled for `/stream/`.
- Invite rejected: inspect expiry, revocation, max-use state, and audit events.

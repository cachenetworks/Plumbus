#!/usr/bin/env sh
set -eu

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Engine and the Compose plugin first." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin is required." >&2
  exit 1
fi

if [ ! -f .env ]; then
  db_password="$(openssl rand -hex 24 2>/dev/null || od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  session_secret="$(openssl rand -hex 32 2>/dev/null || od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  encryption_key="$(openssl rand -hex 32 2>/dev/null || od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
  cat > .env <<EOF
APP_ENV=production
APP_URL=http://localhost:8080
HTTP_PORT=8080
POSTGRES_DB=cinema
POSTGRES_USER=cinema
POSTGRES_PASSWORD=${db_password}
DATABASE_URL=postgresql+psycopg://cinema:${db_password}@postgres:5432/cinema
REDIS_URL=redis://redis:6379/0
SESSION_SECRET=${session_secret}
TOKEN_ENCRYPTION_KEY=${encryption_key}
PLAYBACK_TOKEN_LIFETIME_MINUTES=360
PLEX_SCAN_INTERVAL_MINUTES=30
COOKIE_SECURE=true
CORS_ORIGINS=http://localhost:8080
MOCK_PLEX=false
WEB_CONCURRENCY=4
GUNICORN_TIMEOUT=120
CELERY_CONCURRENCY=2
CELERY_LOG_LEVEL=INFO
EOF
  chmod 600 .env
  echo "Created .env with generated infrastructure secrets."
else
  echo "Using existing .env."
fi

docker compose up -d --build

echo
echo "Plumbus is starting."
echo "Open: http://localhost:${HTTP_PORT:-8080}/setup"
echo "Get the first-run setup code with:"
echo "  docker compose logs backend | grep 'SETUP CODE' | tail -1"
echo
echo "The web wizard will configure the public URL, Discord, Plex sign-in, server, libraries and playback defaults."

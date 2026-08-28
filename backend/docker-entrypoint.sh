#!/bin/sh
set -eu

MODE="${1:-web}"

case "$MODE" in
  web)
    echo "[plumbus] applying database migrations"
    alembic upgrade head
    echo "[plumbus] starting API"
    exec gunicorn app.main:app \
      -k uvicorn.workers.UvicornWorker \
      -w "${WEB_CONCURRENCY:-4}" \
      -b 0.0.0.0:8000 \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --graceful-timeout 30 \
      --keep-alive 5 \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    echo "[plumbus] starting Celery worker"
    exec celery -A app.workers.celery_app:celery_app worker \
      --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
      --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    echo "[plumbus] starting Celery beat"
    exec celery -A app.workers.celery_app:celery_app beat \
      --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
      --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac

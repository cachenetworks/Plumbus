from app.workers.celery_app import celery_app


def test_worker_registers_plumbus_tasks() -> None:
    # Celery workers are started from celery_app.py, so the task module must be
    # explicitly included or queued scans are discarded as unregistered tasks.
    assert "app.workers.tasks" in celery_app.conf.include

    celery_app.loader.import_default_modules()

    assert "app.workers.tasks.scan_library" in celery_app.tasks
    assert "app.workers.tasks.sync_enabled_libraries" in celery_app.tasks
    assert "app.workers.tasks.refresh_plex_account" in celery_app.tasks

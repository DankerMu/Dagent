"""A worker ping exception means enqueue is unavailable."""

from __future__ import annotations

from types import SimpleNamespace

from xagent.config import CELERY_BROKER_URL, CELERY_ENABLED
from xagent.web.services.background_jobs import is_background_job_enqueue_available


def test_worker_ping_exception_means_enqueue_unavailable(monkeypatch):
    """check_worker=True must fail closed when the Celery ping itself raises.

    Existing tests cover an unanswered ping returning falsey, which never
    enters the exception handler. A broker that throws on ping (import
    failure, transport error) must still report enqueue unavailable rather
    than leaking the exception to the caller.
    """
    monkeypatch.setenv(CELERY_ENABLED, "true")
    monkeypatch.setenv(CELERY_BROKER_URL, "memory://")

    class _ExplodingControl:
        def ping(self, timeout=0.5):
            raise RuntimeError("broker transport closed")

    exploding_app = SimpleNamespace(
        conf=SimpleNamespace(task_always_eager=False),
        control=_ExplodingControl(),
    )
    monkeypatch.setattr(
        "xagent.web.jobs.celery_app.celery_app",
        exploding_app,
    )

    assert is_background_job_enqueue_available(check_worker=False) is True
    assert is_background_job_enqueue_available(check_worker=True) is False

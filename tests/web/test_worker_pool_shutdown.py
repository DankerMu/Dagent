"""Supervision must reap children, including a child ignoring termination."""

from xagent.web import worker_pool


def test_shutdown_force_kills_unresponsive_worker_without_abandoning_other_children(
    monkeypatch,
):
    events = []

    class Child:
        def __init__(self, name, *, stubborn=False):
            self.name = name
            self.pid = 123
            self.stubborn = stubborn
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            events.append((self.name, "terminate"))
            if not self.stubborn:
                self.alive = False

        def join(self, timeout=None):
            events.append((self.name, "join"))

        def kill(self):
            events.append((self.name, "kill"))
            self.alive = False

        def close(self):
            events.append((self.name, "close"))

    monkeypatch.setattr(worker_pool.time, "monotonic", lambda: 100.0)
    worker_pool._stop_processes([Child("web"), Child("worker", stubborn=True)])

    assert events == [
        ("web", "terminate"),
        ("worker", "terminate"),
        ("web", "join"),
        ("worker", "join"),
        ("worker", "kill"),
        ("web", "join"),
        ("web", "close"),
        ("worker", "join"),
        ("worker", "close"),
    ]

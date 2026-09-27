"""Requested JavaScript packages never trigger implicit registry access."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from xagent.core.tools.core.javascript_executor import JavaScriptExecutorCore


def test_missing_npm_cache_never_contacts_configured_registry(tmp_path, monkeypatch):
    requests = []

    class Registry(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    user_config = tmp_path / "npmrc"
    user_config.write_text("")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(user_config))
    monkeypatch.setenv("NPM_CONFIG_CACHE", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("NPM_CONFIG_OFFLINE", "false")
    monkeypatch.setenv("NPM_CONFIG_FETCH_RETRIES", "0")
    monkeypatch.setenv("NPM_CONFIG_FETCH_TIMEOUT", "1000")
    try:
        result = JavaScriptExecutorCore().execute_code(
            "console.log(19 + 23)", packages=["xagent-offline-cache-proof"]
        )
        assert result["success"] is True, result
        assert result["output"].strip() == "42"
        assert requests == [], (
            "Execution must not query even an explicitly configured registry"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

"""Local authoring survives exported-route hydration and browser history."""

import json
import time
from urllib.parse import urlsplit

import pytest

from tests.e2e.runtime_proof import PROOF_OWNER_PASSWORD, PROOF_OWNER_USERNAME
from tests.e2e.test_ui_smoke import (
    _console_errors,
    _observe_request_origins,
    _require_playwright_chromium,
)

pytestmark = [pytest.mark.e2e, pytest.mark.ui_smoke]


def _navigation_trace(page):
    """Capture sanitized request ordering, not headers, query values or bodies."""
    started = time.monotonic()
    events = []
    requests = {}

    def record(kind, url="", detail=None, request=None):
        request_id = (
            requests.setdefault(request, len(requests) + 1) if request else None
        )
        events.append(
            {
                "elapsed": round(time.monotonic() - started, 6),
                "kind": kind,
                "path": urlsplit(url).path,
                "detail": detail,
                "request_id": request_id,
            }
        )

    page.on("request", lambda r: record("request", r.url, request=r))
    page.on("requestfinished", lambda r: record("finished", r.url, request=r))
    page.on("requestfailed", lambda r: record("failed", r.url, r.failure, r))
    page.on("response", lambda r: record("response", r.url, r.status, r.request))
    page.on(
        "framenavigated",
        lambda f: record("navigation", f.url) if f == page.main_frame else None,
    )
    page.on(
        "console",
        lambda m: (
            record("console-error", page.url)
            if m.type == "error"
            else record("pagehide", page.url)
            if m.text == "[DEBUG-navigation-pagehide]"
            else None
        ),
    )
    page.add_init_script(
        "addEventListener('pagehide', () => console.debug('[DEBUG-navigation-pagehide]'))"
    )
    return events


def test_local_skill_browser_roundtrip(ui_proof_app):
    app = ui_proof_app
    origin = str(app.client.base_url).rstrip("/")
    out = app.root / "skill-ui-artifacts"
    out.mkdir(exist_ok=True)
    playwright, browser = _require_playwright_chromium()
    page = browser.new_page()
    origins = _observe_request_origins(page, origin)
    skill_requests = []
    page.on(
        "request",
        lambda request: (
            skill_requests.append(request.url.split("?")[0])
            if "/api/skills/" in request.url
            else None
        ),
    )
    errors = _console_errors(page)
    events = _navigation_trace(page)
    content = "---\nname: offline-authored\ndescription: Local authoring proof\n---\n# Local skill\n\nUse local documents.\n"
    try:
        page.goto(origin + "/login")
        page.fill('input[name="identifier"]', PROOF_OWNER_USERNAME)
        page.fill('input[name="password"]', PROOF_OWNER_PASSWORD)
        page.get_by_role("button", name="Log In", exact=True).click()
        page.get_by_role("button", name="Skip setup", exact=True).click(timeout=30000)
        page.wait_for_url(origin + "/task")
        page.goto(origin + "/skills/new")
        page.get_by_label("Skill name", exact=False).fill("offline-authored")
        page.locator("textarea").fill(content)
        page.get_by_role("button", name="Create skill", exact=True).click()
        page.wait_for_url(origin + "/skills/offline-authored")
        page.get_by_role("button", name="Edit SKILL.md", exact=True).click()
        updated = content + "\nEdited through the actual browser.\n"
        page.locator("textarea").fill(updated)
        page.get_by_role("button", name="Save", exact=True).click()
        page.get_by_role("button", name="Edit SKILL.md", exact=True).wait_for()
        detail = app.client.get(
            "/api/skills/installed/offline-authored", headers=app.headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["content"] == updated
        page.screenshot(path=str(out / "edited-skill.png"))
        page.reload()
        page.get_by_role("heading", name="offline-authored", exact=True).wait_for()
        page.goto(origin + "/skills/new")
        page.get_by_label("Skill name", exact=False).fill("offline-imported")
        page.locator("input[type=file]").set_input_files(
            {
                "name": "SKILL.md",
                "mimeType": "text/markdown",
                "buffer": content.encode(),
            }
        )
        page.wait_for_url(origin + "/skills/offline-imported")
        imported = app.client.get(
            "/api/skills/installed/offline-imported", headers=app.headers
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["content"] == content
        page.screenshot(path=str(out / "imported-skill.png"))
        page.get_by_role("link", name="All skills", exact=True).click()
        page.wait_for_url(origin + "/skills")
        page.get_by_role("link", name="offline-authored", exact=False).click()
        page.wait_for_url(origin + "/skills/offline-authored")
        page.get_by_role("heading", name="offline-authored", exact=True).wait_for()
        page.go_back()
        page.wait_for_url(origin + "/skills")
        page.go_back()
        page.wait_for_url(origin + "/skills/offline-imported")
        page.get_by_role("heading", name="offline-imported", exact=True).wait_for()
        for name in ("offline-imported", "offline-authored"):
            page.goto(origin + "/skills/" + name)
            page.get_by_role("button", name="Delete", exact=True).click()
            page.get_by_role("alertdialog").get_by_role(
                "button", name="Delete", exact=True
            ).click()
            page.wait_for_url(origin + "/skills")
            assert (
                app.client.get(
                    "/api/skills/installed/" + name, headers=app.headers
                ).status_code
                == 404
            )
        assert all(not url.endswith(("/__shell__", "/new")) for url in skill_requests)
        assert not origins["unexpected"], origins
        assert not errors, errors
    finally:
        (out / "navigation-events.json").write_text(
            json.dumps({"browser": browser.version, "events": events}, indent=2)
        )
        page.screenshot(path=str(out / "final.png"))
        (out / "final-dom.html").write_text(page.content())
        (out / "request-origins.json").write_text(
            json.dumps({k: sorted(v) for k, v in origins.items()}, indent=2)
        )
        browser.close()
        playwright.stop()

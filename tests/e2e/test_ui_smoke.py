"""Browser smoke against the real host serving the Next static export."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.e2e.runtime_proof import (
    COLLECTION_NAME,
    PROOF_OWNER_PASSWORD,
    PROOF_OWNER_USERNAME,
)

pytestmark = [pytest.mark.e2e, pytest.mark.ui_smoke]


def _artifact_dir(app) -> Path:
    directory = app.root / "ui-artifacts"
    directory.mkdir(exist_ok=True)
    return directory


def _require_playwright_chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.fail(
            "Python Playwright is required for UI smoke. Install the browser extra "
            "and Chromium with `python -m playwright install chromium`."
        )
    try:
        playwright = sync_playwright().start()
    except Exception as exc:
        pytest.fail(f"Playwright failed to start: {exc}")
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:
        playwright.stop()
        pytest.fail(
            "Chromium is required for UI smoke. Install it with "
            f"`python -m playwright install chromium`: {exc}"
        )
    return playwright, browser


def _console_errors(page) -> list[str]:
    messages: list[str] = []

    def _on_console(message) -> None:
        if message.type == "error":
            location = urlsplit(message.location.get("url", "")).path
            messages.append(f"{message.text} [{location}]")

    page.on("console", _on_console)
    page.on("pageerror", lambda error: messages.append(str(error)))
    return messages


def _seed_collection(app) -> None:
    created = app.client.post(
        f"/api/kb/collections/{COLLECTION_NAME}/config",
        headers=app.headers,
        json={"chunk_size": 1000, "chunk_overlap": 200},
    )
    assert created.status_code == 200, created.text


def test_browser_login_home_task_and_kb_interactions(ui_proof_app):
    app = ui_proof_app
    assert app.client is not None
    _seed_collection(app)
    origin = str(app.client.base_url).rstrip("/")
    artifacts = _artifact_dir(app)
    playwright, browser = _require_playwright_chromium()
    page = browser.new_page()
    errors = _console_errors(page)
    try:
        page.goto(f"{origin}/login", wait_until="domcontentloaded", timeout=30_000)
        page.locator('input[name="identifier"]').wait_for(timeout=30_000)
        page.screenshot(path=str(artifacts / "login.png"))
        page.fill('input[name="identifier"]', PROOF_OWNER_USERNAME)
        page.fill('input[name="password"]', PROOF_OWNER_PASSWORD)
        page.get_by_role("button", name="Log In").click()
        # A newly seeded account completes the real first-login onboarding exit.
        page.get_by_role("button", name="Skip setup").click(timeout=30_000)
        page.wait_for_url(f"{origin}/task", timeout=30_000)
        page.goto(f"{origin}/", wait_until="domcontentloaded", timeout=30_000)
        page.get_by_role("main").get_by_role("link", name="Task", exact=True).wait_for(
            timeout=30_000
        )
        page.screenshot(path=str(artifacts / "home.png"))
        page.get_by_role("main").get_by_role("link", name="Task", exact=True).click()
        page.wait_for_url(f"{origin}/task", timeout=30_000)
        page.wait_for_selector('[contenteditable="true"]', timeout=30_000)
        page.locator('[contenteditable="true"]').first.fill(
            "UI smoke composer interaction"
        )
        page.screenshot(path=str(artifacts / "task.png"))
        page.goto(f"{origin}/", wait_until="domcontentloaded", timeout=30_000)
        page.get_by_role("main").get_by_role(
            "link", name="Knowledge Base", exact=True
        ).click()
        page.wait_for_url(f"{origin}/kb", timeout=30_000)
        page.get_by_text("Knowledge Base Management").wait_for(timeout=30_000)
        page.get_by_placeholder("Search knowledge base...").fill(COLLECTION_NAME)
        page.get_by_role("heading", name=COLLECTION_NAME).wait_for(timeout=30_000)
        page.get_by_role("heading", name=COLLECTION_NAME).click()
        page.screenshot(path=str(artifacts / "kb.png"))
        assert not errors, f"Browser console errors: {errors}"
    finally:
        try:
            page.screenshot(path=str(artifacts / "final.png"))
        except Exception:
            pass
        browser.close()
        playwright.stop()

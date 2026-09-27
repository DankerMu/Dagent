"""Packaged browser startup fails closed and preserves non-installation errors."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xagent.core.tools.core import browser_use
from xagent.core.tools.core.browser_resources import (
    launch_packaged_chromium,
    start_playwright,
)


@pytest.mark.asyncio
async def test_missing_driver_is_a_prerequisite_error(monkeypatch):
    monkeypatch.setattr(browser_use, "PLAYWRIGHT_AVAILABLE", False)
    monkeypatch.setattr(browser_use, "async_playwright", None)
    session = browser_use.BrowserSession("missing-driver")
    with pytest.raises(RuntimeError):
        await session.get_page()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_binary", [True, False])
async def test_session_distinguishes_missing_binary_from_launch_failure(
    monkeypatch, missing_binary
):
    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    original = PermissionError(
        "Executable doesn't exist at /prepared/chromium"
        if missing_binary
        else "Browser profile directory is not writable"
    )
    driver = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(side_effect=original)),
        stop=AsyncMock(),
    )
    monkeypatch.setattr(browser_use, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(
        browser_use,
        "async_playwright",
        lambda: SimpleNamespace(start=AsyncMock(return_value=driver)),
    )
    session = browser_use.BrowserSession("unavailable-browser")
    try:
        with pytest.raises(
            RuntimeError if missing_binary else PermissionError
        ) as error:
            await session.get_page()
        if missing_binary:
            assert error.value.__cause__ is original
        else:
            assert error.value is original
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_prepared_browser_executes_local_dom_without_downloads(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    playwright_api = pytest.importorskip("playwright.async_api")
    driver = await start_playwright(playwright_api.async_playwright, True)
    browser = None
    try:
        if not Path(driver.chromium.executable_path).is_file():
            pytest.skip("Optional Chromium prerequisite was not prepared on this host")
        browser = await launch_packaged_chromium(driver, headless=True)
        page = await browser.new_page()
        await page.set_content(
            "<button onclick=\"document.querySelector('output').textContent=19+23\">"
            "Compute</button><output>pending</output>"
        )
        await page.get_by_role("button", name="Compute").click()
        assert await page.locator("output").text_content() == "42"
    finally:
        if browser is not None:
            await browser.close()
        await driver.stop()

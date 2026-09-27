"""Fail-closed Playwright startup for locally packaged Chromium."""

import os
from typing import Any


async def start_playwright(factory: Any, available: bool) -> Any:
    """Start the installed driver without allowing a runtime browser download."""
    if not available:
        raise RuntimeError(
            "Playwright is not installed. Install at build/install time "
            "with `pip install playwright` and `playwright install chromium`. "
            "Runtime will not download browsers."
        )
    os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    return await factory().start()


async def launch_packaged_chromium(playwright: Any, headless: bool) -> Any:
    """Launch the local executable, identifying missing prepared assets explicitly."""
    try:
        return await playwright.chromium.launch(
            headless=headless,
            args=[
                # Disable WebDriver detection
                "--disable-blink-features=AutomationControlled",
                # Other anti-detection flags
                "--disable-infobars",
                "--window-size=1920,1080",
                # Allow local file access
                "--allow-file-access-from-files",
                "--allow-file-access",
                # No sandbox for local file access in some environments
                "--no-sandbox",
                # Disable web security for file:// URLs (required for local files)
                "--disable-web-security",
            ],
        )
    except Exception as exc:
        if "Executable doesn't exist" not in str(exc):
            raise
        raise RuntimeError(
            "Playwright Chromium is not packaged on this host. "
            "Install at build/install time with `playwright install chromium` "
            "(the Docker backend image bakes browsers under "
            "/ms-playwright and sets PLAYWRIGHT_BROWSERS_PATH). "
            "Runtime will not download browsers."
        ) from exc

"""Run inside the built ARM64 image, offline and without production configuration."""

import platform
import subprocess
import sys

from playwright.sync_api import sync_playwright

assert platform.machine() == "aarch64"
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False, args=["--no-sandbox"])
    page = browser.new_page()
    page.set_content("<h1>Worker browser ready</h1>")
    assert page.locator("h1").inner_text() == "Worker browser ready"
    browser.close()
subprocess.run([sys.executable, "-m", "app.worker", "--help"], check=True)
print("ARM64 image: headed Chromium and worker entry point passed without network access")

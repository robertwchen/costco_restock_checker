"""Run inside the built ARM64 image, offline and without production configuration."""

import platform
import subprocess
import sys

from playwright.sync_api import sync_playwright

assert platform.machine() == "aarch64"
print("ARM64 container started; launching headed Chromium", flush=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=False, args=["--no-sandbox"], timeout=30000
    )
    print("Chromium launched; checking local HTML", flush=True)
    page = browser.new_page()
    page.set_default_timeout(10000)
    page.set_content("<h1>Worker browser ready</h1>")
    assert page.locator("h1").inner_text() == "Worker browser ready"
    browser.close()
print("Chromium passed; checking worker entry point", flush=True)
subprocess.run([sys.executable, "-m", "app.worker", "--help"], check=True, timeout=15)
print("ARM64 image: headed Chromium and worker entry point passed without network access")

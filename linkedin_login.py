import json
import os
import sys

from playwright.sync_api import sync_playwright

USER_DATA_DIR = os.path.join(os.path.dirname(__file__), "browser_data")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5],
});
window.chrome = { runtime: {} };
"""


def main():
    with sync_playwright() as p:
        launch_kwargs = {
            "headless": False,
            "user_agent": USER_AGENT,
            "viewport": {"width": 1366, "height": 768},
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }

        try:
            context = p.chromium.launch_persistent_context(USER_DATA_DIR, channel="chrome", **launch_kwargs)
            print("Using installed Google Chrome.")
        except Exception:
            context = p.chromium.launch_persistent_context(USER_DATA_DIR, **launch_kwargs)
            print("Google Chrome not found; using Playwright Chromium.")

        context.add_init_script(STEALTH_SCRIPT)

        def on_page(page):
            print(f"New page/popup opened: {page.url}")

        context.on("page", on_page)

        config = {}
        if os.path.exists("config.json"):
            with open("config.json", "r", encoding="utf-8") as f:
                config = json.load(f)

        page = context.new_page()
        print("Opening LinkedIn...")
        page.goto("https://www.linkedin.com/home")
        page.wait_for_timeout(config.get("page_load_wait_ms", 3000))

        print("\nLog in to LinkedIn in the browser window (Google sign-in should work now).")
        input("Press Enter here once you're logged in...\n")

        try:
            context.storage_state(path="linkedin_state.json")
            print("Session saved to linkedin_state.json")
        except Exception as e:
            print(f"Could not save session: {e}")

        context.close()
        print("Done.")


if __name__ == "__main__":
    main()

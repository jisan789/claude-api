import json
import os
import sys
from playwright.sync_api import sync_playwright

def format_cookie(c):
    cookie = {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "httpOnly": c.get("httpOnly", False),
        "secure": c.get("secure", False),
    }

    # Playwright requires sameSite to be "Strict", "Lax", or "None"
    same_site = c.get("sameSite", "").lower()
    if same_site in ("no_restriction", "none"):
        cookie["sameSite"] = "None"
    elif same_site == "lax":
        cookie["sameSite"] = "Lax"
    elif same_site == "strict":
        cookie["sameSite"] = "Strict"

    # Playwright accepts 'expires' in seconds as float or integer
    if not c.get("session") and "expirationDate" in c and c["expirationDate"]:
        cookie["expires"] = float(c["expirationDate"])

    return cookie

def main():
    cookie_file = os.path.join(os.path.dirname(__file__), "cookies.json")
    if not os.path.exists(cookie_file):
        print(f"Error: {cookie_file} not found.")
        sys.exit(1)

    with open(cookie_file, "r", encoding="utf-8") as f:
        raw_cookies = json.load(f)

    playwright_cookies = [format_cookie(c) for c in raw_cookies]

    print(f"Loaded {len(playwright_cookies)} cookies.")
    print("Launching Chromium...")

    with sync_playwright() as p:
        # Launch Chromium browser with UI (headless=False)
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        )

        # Inject cookies
        context.add_cookies(playwright_cookies)
        print("Cookies successfully added to browser context.")

        page = context.new_page()
        target_url = "https://claude.ai/new"
        print(f"Navigating to {target_url} ...")
        
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            print("Successfully loaded page!")
            print("Chromium window is active. Close the browser window to exit the script.")
            # Keep browser alive until user closes it
            page.wait_for_close(timeout=0)
        except KeyboardInterrupt:
            print("\nExiting on user interrupt...")
        except Exception as e:
            print(f"Browser session ended: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    main()

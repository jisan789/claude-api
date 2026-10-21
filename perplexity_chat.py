import argparse
import json
import os
import sys
import time

try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

INPUT_SELECTOR = "#ask-input, textarea[placeholder*='Ask'], textarea[placeholder*='Search'], textarea"

def format_cookie(c):
    cookie = {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
        "httpOnly": c.get("httpOnly", False),
        "secure": c.get("secure", False),
    }

    same_site = c.get("sameSite", "").lower()
    if same_site in ("no_restriction", "none"):
        cookie["sameSite"] = "None"
    elif same_site == "lax":
        cookie["sameSite"] = "Lax"
    elif same_site == "strict":
        cookie["sameSite"] = "Strict"

    if not c.get("session") and "expirationDate" in c and c["expirationDate"]:
        cookie["expires"] = float(c["expirationDate"])

    return cookie

def dismiss_cookie_banner(page):
    try:
        got_it = page.locator('button:has-text("Got it")')
        if got_it.count() > 0 and got_it.first.is_visible():
            got_it.first.click()
            time.sleep(0.4)
    except Exception:
        pass

def is_cloudflare_challenge(page) -> bool:
    try:
        title = page.title().lower()
        if "just a moment" in title or "attention required" in title:
            return True
        if page.locator("iframe[src*='challenges.cloudflare.com']").count() > 0:
            return True
        body = page.locator("body").inner_text().lower()
        if (
            "performing security verification" in body
            or "verify you are human" in body
            or "verify you are not a bot" in body
        ):
            return True
    except Exception:
        pass
    return False

def resolve_cloudflare_challenge(page, timeout: int = 60) -> bool:
    if not is_cloudflare_challenge(page):
        return True

    title = page.title()
    print(f"[Cloudflare] Turnstile challenge detected (Title: '{title}'). Attempting automatic bypass...")
    start_time = time.time()
    last_click_time = 0

    while time.time() - start_time < timeout:
        current_title = page.title()
        current_title_lower = current_title.lower()

        if "just a moment" not in current_title_lower and "attention required" not in current_title_lower:
            input_count = page.locator(INPUT_SELECTOR).count()
            if input_count > 0 or "perplexity" in current_title_lower:
                print(f"[Cloudflare] Security verification passed! Page title: '{current_title}'")
                return True

        now = time.time()
        if now - last_click_time >= 3.5:
            clicked = False
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url:
                    for sel in ["input[type='checkbox']", ".cb-i", "label", "#challenge-stage"]:
                        try:
                            cb = frame.locator(sel).first
                            if cb.count() > 0 and cb.is_visible():
                                box = cb.bounding_box()
                                if box:
                                    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=5)
                                    time.sleep(0.2)
                                    cb.click(force=True)
                                    print(f"[Cloudflare] Clicked challenge checkbox inside Turnstile frame ('{sel}').")
                                    clicked = True
                                    last_click_time = now
                                    break
                        except Exception:
                            pass
                    if clicked:
                        break

            if not clicked:
                try:
                    iframe = page.locator("iframe[src*='challenges.cloudflare.com']").first
                    if iframe.count() > 0 and iframe.is_visible():
                        box = iframe.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            click_x = box["x"] + min(28, box["width"] * 0.15)
                            click_y = box["y"] + (box["height"] / 2)
                            page.mouse.move(click_x, click_y, steps=5)
                            time.sleep(0.2)
                            page.mouse.click(click_x, click_y)
                            print(f"[Cloudflare] Clicked Turnstile widget coordinates ({int(click_x)}, {int(click_y)}).")
                            last_click_time = now
                except Exception:
                    pass

        elapsed = time.time() - start_time
        if 25 <= elapsed <= 28:
            print("[Cloudflare] Turnstile still pending after 25s, reloading page...")
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
            except Exception:
                pass

        time.sleep(1.0)

    final_title = page.title()
    if "just a moment" not in final_title.lower():
        return True

    print(f"[Cloudflare] Timed out waiting for Cloudflare challenge to resolve after {timeout}s.")
    return False

def wait_for_perplexity_input(page, timeout: int = 45000):
    if is_cloudflare_challenge(page):
        resolve_cloudflare_challenge(page, timeout=int(timeout / 1000))

    input_el = page.locator(INPUT_SELECTOR).first
    input_el.wait_for(state="visible", timeout=timeout)
    dismiss_cookie_banner(page)
    return input_el

def send_prompt_and_get_reply(page, prompt_text):
    """
    Submits prompt_text to Perplexity and streams response in real-time.
    """
    print(f"\n[You]: {prompt_text}")

    dismiss_cookie_banner(page)

    # 1. Count prior responses
    prior_count = page.locator(".prose").count()

    # 2. Locate input box
    input_el = wait_for_perplexity_input(page, timeout=30000)

    # 3. Focus and type prompt
    input_el.click()
    page.keyboard.type(prompt_text, delay=10)
    time.sleep(0.3)

    # 4. Click Submit button or press Enter
    submit_btn = page.locator('button[aria-label="Submit"]')
    if submit_btn.count() > 0 and submit_btn.first.is_visible():
        submit_btn.first.click()
    else:
        page.keyboard.press("Enter")

    print("\n[Perplexity]: ", end="", flush=True)

    # 5. Wait for the new response to appear
    start_time = time.time()
    while page.locator(".prose").count() <= prior_count:
        if time.time() - start_time > 35:
            print("\nError: Timed out waiting for Perplexity to begin responding.")
            return ""
        time.sleep(0.2)

    new_response = page.locator(".prose").nth(prior_count)

    # 6. Stream text live and wait until completion
    printed_len = 0
    stable_cycles = 0
    full_text = ""
    last_text = ""

    while True:
        try:
            current_text = new_response.evaluate("""el => {
                const clone = el.cloneNode(true);
                clone.querySelectorAll('.citation, [data-pplx-citation]').forEach(e => e.remove());
                return clone.innerText.trim();
            }""")
        except Exception:
            current_text = ""

        if current_text:
            if len(current_text) > printed_len:
                new_chunk = current_text[printed_len:]
                print(new_chunk, end="", flush=True)
                printed_len = len(current_text)
                full_text = current_text
                stable_cycles = 0
                last_text = current_text
            elif current_text == last_text and printed_len > 0:
                stable_cycles += 1
                if stable_cycles >= 3:
                    break
            else:
                stable_cycles = 0
                last_text = current_text

        time.sleep(0.4)

    print("\n" + "-" * 50)
    return full_text

def run_perplexity_bot(invisible=True, initial_prompt=None):
    cookie_file = os.path.join(os.path.dirname(__file__), "cookies.json")
    if not os.path.exists(cookie_file):
        print(f"Error: {cookie_file} not found.")
        sys.exit(1)

    with open(cookie_file, "r", encoding="utf-8") as f:
        raw_cookies = json.load(f)

    playwright_cookies = [format_cookie(c) for c in raw_cookies]

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--lang=en-US,en",
        "--js-flags=--max-old-space-size=256",
        "--renderer-process-limit=2",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-translate",
        "--no-first-run",
        "--mute-audio",
    ]
    if invisible:
        print("Starting in background (invisible mode - no browser window)...")
        launch_args.append("--window-position=-20000,-20000")
    else:
        print("Starting with visible browser window...")

    proxy_url = os.getenv("PROXY_URL") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    proxy_config = {"server": proxy_url} if proxy_url else None
    if proxy_config:
        print(f"Using proxy: {proxy_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=launch_args,
            proxy=proxy_config,
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )

        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32'
            });
            Object.defineProperty(navigator, 'vendor', {
                get: () => 'Google Inc.'
            });
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 4
            });
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            if (navigator.userAgentData) {
                Object.defineProperty(navigator.userAgentData, 'platform', {
                    get: () => 'Windows'
                });
            }
            if (!window.chrome) {
                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };
            }
        """)

        context.add_cookies(playwright_cookies)

        page = context.new_page()
        print("Connecting to https://www.perplexity.ai/ ...")
        page.goto("https://www.perplexity.ai/", wait_until="domcontentloaded", timeout=60000)

        # Wait for chat input (resolving any Cloudflare challenge)
        wait_for_perplexity_input(page, timeout=45000)
        print("Connected & Logged in successfully!")

        # Single prompt execution
        if initial_prompt:
            send_prompt_and_get_reply(page, initial_prompt)
            browser.close()
            return

        # Interactive terminal loop
        print("\nReady! Enter your prompt below (or type 'exit' to quit):")
        print("=" * 50)
        try:
            while True:
                prompt = input("\nEnter prompt: ").strip()
                if not prompt:
                    continue
                if prompt.lower() in ("exit", "quit"):
                    break

                send_prompt_and_get_reply(page, prompt)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting session...")
        finally:
            browser.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Perplexity AI CLI automation")
    parser.add_argument("prompt", nargs="?", default=None, help="Optional prompt to send directly")
    parser.add_argument("--visible", action="store_true", help="Show the browser window (default is invisible)")
    args = parser.parse_args()

    run_perplexity_bot(invisible=not args.visible, initial_prompt=args.prompt)

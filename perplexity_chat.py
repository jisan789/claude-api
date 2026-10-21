import argparse
import json
import os
import sys
import time
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

def send_prompt_and_get_reply(page, prompt_text):
    """
    Submits prompt_text to Perplexity (#ask-input) and streams response in real-time.
    """
    print(f"\n[You]: {prompt_text}")

    dismiss_cookie_banner(page)

    # 1. Count prior responses
    prior_count = page.locator(".prose").count()

    # 2. Locate input box
    input_el = page.locator("#ask-input").first
    input_el.wait_for(state="visible", timeout=30000)

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

    launch_args = ["--disable-blink-features=AutomationControlled"]
    if invisible:
        print("Starting in background (invisible mode - no browser window)...")
        launch_args.append("--window-position=-20000,-20000")
    else:
        print("Starting with visible browser window...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=launch_args,
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        )

        context.add_cookies(playwright_cookies)

        page = context.new_page()
        print("Connecting to https://www.perplexity.ai/ ...")
        page.goto("https://www.perplexity.ai/", wait_until="domcontentloaded", timeout=60000)

        # Wait for chat input
        page.wait_for_selector("#ask-input", timeout=45000)
        dismiss_cookie_banner(page)
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

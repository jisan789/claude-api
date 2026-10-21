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

def send_prompt_and_get_reply(page, prompt_text):
    """
    Inputs prompt_text into Claude's chat input:
    <div data-testid="chat-input" class="tiptap ProseMirror" contenteditable="true">
    Submits via:
    <button data-testid="chat-input-send">
    Streams response live to console until complete.
    """
    print(f"\n[You]: {prompt_text}")

    # 1. Count how many assistant messages exist BEFORE sending
    prior_count = page.locator('div[data-perf-row="assistant"]').count()

    # 2. Locate input box
    input_el = page.locator('[data-testid="chat-input"]').first
    input_el.wait_for(state="visible", timeout=30000)

    # 3. Focus and type prompt
    input_el.click()
    page.keyboard.type(prompt_text, delay=15)
    page.wait_for_timeout(300)

    # 4. Click send button
    send_btn = page.locator('button[data-testid="chat-input-send"]')
    send_btn.wait_for(state="visible", timeout=10000)
    send_btn.click()

    print("\n[Claude]: ", end="", flush=True)

    # 5. Wait for the NEW assistant row (index == prior_count) to appear
    start_time = time.time()
    while page.locator('div[data-perf-row="assistant"]').count() <= prior_count:
        if time.time() - start_time > 30:
            print("\nError: Timed out waiting for Claude to begin responding.")
            return ""
        time.sleep(0.2)

    new_assistant_row = page.locator('div[data-perf-row="assistant"]').nth(prior_count)

    # 6. Stream text live and wait until completion
    printed_len = 0
    stable_cycles = 0
    full_text = ""

    while True:
        # Locate text element inside the new assistant row
        reply_el = new_assistant_row.locator('[data-perf-reply-text]').or_(
            new_assistant_row.locator('.standard-markdown')
        )

        if reply_el.count() > 0:
            current_text = reply_el.inner_text()
            if len(current_text) > printed_len:
                # Print newly generated chunk
                new_chunk = current_text[printed_len:]
                print(new_chunk, end="", flush=True)
                printed_len = len(current_text)
                full_text = current_text
                stable_cycles = 0

        # Check completion indicators on the NEW message row:
        # - data-perf-row-streaming is "false"
        # - Action bar Copy button has rendered inside this assistant row
        streaming_attr = new_assistant_row.get_attribute("data-perf-row-streaming")
        has_copy_btn = new_assistant_row.locator('button[aria-label="Copy"]').count() > 0

        if streaming_attr == "false" and has_copy_btn and printed_len > 0:
            stable_cycles += 1
            if stable_cycles >= 2:  # Stable for ~0.8s after finish
                break
        else:
            stable_cycles = 0

        time.sleep(0.4)

    print("\n" + "-" * 50)
    return full_text

def run_claude_bot(invisible=True, initial_prompt=None):
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
        print("Connecting to https://claude.ai/new ...")
        page.goto("https://claude.ai/new", wait_until="domcontentloaded", timeout=60000)

        # Wait for chat input
        input_el = page.locator('[data-testid="chat-input"]').first
        input_el.wait_for(state="visible", timeout=45000)
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
    parser = argparse.ArgumentParser(description="Claude.ai CLI automation")
    parser.add_argument("prompt", nargs="?", default=None, help="Optional prompt to send directly")
    parser.add_argument("--visible", action="store_true", help="Show the browser window (default is invisible)")
    args = parser.parse_args()

    run_claude_bot(invisible=not args.visible, initial_prompt=args.prompt)

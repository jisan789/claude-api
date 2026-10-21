import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

app_state = {
    "playwright": None,
    "browser": None,
    "page": None,
    "lock": None,
    "initialized": False,
    "init_error": None,
    "init_task": None,
    "step": "not_started",
}

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

def extract_chat_id(url: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"/search/([a-f0-9\-]+)", url)
    return match.group(1) if match else None

async def dismiss_cookie_banner(page):
    try:
        got_it = page.locator('button:has-text("Got it")')
        if await got_it.count() > 0 and await got_it.first.is_visible():
            await got_it.first.click()
            await asyncio.sleep(0.3)
    except Exception:
        pass

async def is_cloudflare_challenge(page) -> bool:
    try:
        title = (await page.title()).lower()
        if "just a moment" in title or "attention required" in title:
            return True
        if await page.locator("iframe[src*='challenges.cloudflare.com']").count() > 0:
            return True
        body = await page.locator("body").inner_text()
        body_lower = body.lower()
        if (
            "performing security verification" in body_lower
            or "verify you are human" in body_lower
            or "verify you are not a bot" in body_lower
        ):
            return True
    except Exception:
        pass
    return False

async def resolve_cloudflare_challenge(page, timeout: int = 90) -> bool:
    if not await is_cloudflare_challenge(page):
        return True

    title = await page.title()
    print(f"[Cloudflare] Turnstile challenge detected (Title: '{title}'). Attempting automatic bypass...")
    app_state["step"] = "resolving_cloudflare_challenge"
    start_time = time.time()
    last_click_time = 0

    while time.time() - start_time < timeout:
        current_title = await page.title()
        current_title_lower = current_title.lower()

        # Check if challenge cleared
        if "just a moment" not in current_title_lower and "attention required" not in current_title_lower:
            input_count = await page.locator(INPUT_SELECTOR).count()
            if input_count > 0 or "perplexity" in current_title_lower:
                print(f"[Cloudflare] Security verification passed! Page title: '{current_title}'")
                return True

        # Check if turnstile response token was populated
        try:
            has_token = await page.evaluate("""() => {
                const input = document.querySelector('input[name="cf-turnstile-response"]');
                return input && input.value && input.value.length > 0;
            }""")
            if has_token:
                print("[Cloudflare] Turnstile token acquired! Submitting challenge form...")
                await page.evaluate("""() => {
                    const form = document.querySelector('#challenge-form') || document.querySelector('form');
                    if (form) form.submit();
                }""")
                await asyncio.sleep(2)
        except Exception:
            pass

        now = time.time()
        # Attempt interaction every 3.5 seconds
        if now - last_click_time >= 3.5:
            clicked = False

            # Method 1: Check frames for checkbox or interactive elements
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url:
                    for sel in ["input[type='checkbox']", "label", ".cb-i", "#challenge-stage"]:
                        try:
                            cb = frame.locator(sel).first
                            if await cb.count() > 0 and await cb.is_visible():
                                box = await cb.bounding_box()
                                if box:
                                    cx = box["x"] + box["width"] / 2
                                    cy = box["y"] + box["height"] / 2
                                    await page.mouse.move(cx, cy, steps=8)
                                    await asyncio.sleep(0.15)
                                    await page.mouse.down()
                                    await asyncio.sleep(0.08)
                                    await page.mouse.up()
                                    print(f"[Cloudflare] Clicked checkbox inside Turnstile frame ('{sel}').")
                                    clicked = True
                                    last_click_time = now
                                    break
                        except Exception:
                            pass
                    if clicked:
                        break

            # Method 2: Click outer Turnstile container if frame interaction didn't trigger
            if not clicked:
                for sel in ["div.cf-turnstile", "#challenge-stage", "#turnstile-wrapper"]:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0 and await el.is_visible():
                            box = await el.bounding_box()
                            if box and box["width"] > 0 and box["height"] > 0:
                                cx = box["x"] + min(30, box["width"] * 0.15)
                                cy = box["y"] + (box["height"] / 2)
                                await page.mouse.move(cx, cy, steps=8)
                                await asyncio.sleep(0.15)
                                await page.mouse.down()
                                await asyncio.sleep(0.08)
                                await page.mouse.up()
                                print(f"[Cloudflare] Clicked container '{sel}' at ({int(cx)}, {int(cy)}).")
                                clicked = True
                                last_click_time = now
                                break
                    except Exception:
                        pass

            # Method 3: Click widget coordinates on the iframe itself
            if not clicked:
                try:
                    iframe = page.locator("iframe[src*='challenges.cloudflare.com']").first
                    if await iframe.count() > 0 and await iframe.is_visible():
                        box = await iframe.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            click_x = box["x"] + min(28, box["width"] * 0.15)
                            click_y = box["y"] + (box["height"] / 2)
                            await page.mouse.move(click_x, click_y, steps=8)
                            await asyncio.sleep(0.15)
                            await page.mouse.down()
                            await asyncio.sleep(0.08)
                            await page.mouse.up()
                            print(f"[Cloudflare] Clicked Turnstile widget coordinates ({int(click_x)}, {int(click_y)}).")
                            last_click_time = now
                except Exception:
                    pass

        # If stuck on challenge for >40s, try reloading page once
        elapsed = time.time() - start_time
        if 40 <= elapsed <= 43:
            print("[Cloudflare] Turnstile still pending after 40s, reloading page...")
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
            except Exception:
                pass

        await asyncio.sleep(1.0)

    final_title = await page.title()
    if "just a moment" not in final_title.lower():
        return True

    print(f"[Cloudflare] Timed out waiting for Cloudflare challenge to resolve after {timeout}s.")
    return False

async def wait_for_perplexity_input(page, timeout: int = 90000):
    if await is_cloudflare_challenge(page):
        await resolve_cloudflare_challenge(page, timeout=int(timeout / 1000))

    input_el = page.locator(INPUT_SELECTOR).first
    await input_el.wait_for(state="visible", timeout=timeout)
    await dismiss_cookie_banner(page)
    return input_el

async def wait_for_response_completion(response_element) -> str:
    printed_len = 0
    stable_cycles = 0
    full_text = ""
    last_text = ""
    max_wait = 180
    start_time = time.time()

    while time.time() - start_time < max_wait:
        try:
            current_text = await response_element.evaluate("""el => {
                const clone = el.cloneNode(true);
                clone.querySelectorAll('.citation, [data-pplx-citation]').forEach(e => e.remove());
                return clone.innerText.trim();
            }""")
        except Exception:
            current_text = ""

        if current_text:
            if len(current_text) > printed_len:
                full_text = current_text
                printed_len = len(current_text)
                stable_cycles = 0
                last_text = current_text
            elif current_text == last_text and printed_len > 0:
                stable_cycles += 1
                # Stable for ~1.6s
                if stable_cycles >= 4:
                    return full_text
            else:
                stable_cycles = 0
                last_text = current_text

        await asyncio.sleep(0.4)

    if full_text:
        return full_text
    raise TimeoutError("Timed out waiting for Perplexity response to complete.")

async def send_prompt_on_page(page, prompt_text: str) -> dict:
    await dismiss_cookie_banner(page)
    prior_count = await page.locator(".prose").count()

    input_el = await wait_for_perplexity_input(page, timeout=30000)
    await input_el.click()
    await page.keyboard.type(prompt_text, delay=10)
    await asyncio.sleep(0.3)

    submit_btn = page.locator('button[aria-label="Submit"]')
    if await submit_btn.count() > 0 and await submit_btn.first.is_visible():
        await submit_btn.first.click()
    else:
        await page.keyboard.press("Enter")

    wait_start = time.time()
    while await page.locator(".prose").count() <= prior_count:
        if time.time() - wait_start > 35:
            raise TimeoutError("Perplexity did not begin generating a response within 35 seconds.")
        await asyncio.sleep(0.3)

    new_row = page.locator(".prose").nth(prior_count)
    reply_text = await wait_for_response_completion(new_row)

    current_url = page.url
    chat_id = extract_chat_id(current_url)

    return {
        "response": reply_text,
        "chat_id": chat_id,
        "chat_url": current_url,
    }

async def initialize_browser():
    if app_state["initialized"]:
        return

    try:
        app_state["step"] = "reading_cookies"
        raw_cookies_env = (
            os.getenv("PERPLEXITY_COOKIES")
            or os.getenv("PPLX_COOKIES")
            or os.getenv("CLAUDE_COOKIES")
        )
        if raw_cookies_env:
            raw_cookies = json.loads(raw_cookies_env)
        else:
            cookie_file = os.path.join(os.path.dirname(__file__), "cookies.json")
            if os.path.exists(cookie_file):
                with open(cookie_file, "r", encoding="utf-8") as f:
                    raw_cookies = json.load(f)
            else:
                app_state["init_error"] = "PERPLEXITY_COOKIES environment variable not set in Render Dashboard."
                app_state["step"] = "error_missing_cookies"
                print(f"[Notice] {app_state['init_error']}")
                return

        cookies = [format_cookie(c) for c in raw_cookies]

        app_state["step"] = "launching_chromium"
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
        if sys.platform.startswith("win"):
            launch_args.append("--window-position=-20000,-20000")

        proxy_url = os.getenv("PROXY_URL") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        proxy_config = {"server": proxy_url} if proxy_url else None
        if proxy_config:
            print(f"[Startup] Using proxy configuration: {proxy_url}")

        print("[Startup] Launching Chromium engine (with Patchright stealth)...")
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=False,
            args=launch_args,
            proxy=proxy_config,
        )

        app_state["step"] = "creating_context"
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Inject comprehensive stealth scripts matching the Windows Chrome profile
        await context.add_init_script("""
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

        await context.add_cookies(cookies)

        page = await context.new_page()
        app_state["page"] = page
        app_state["browser"] = browser
        app_state["playwright"] = p
        app_state["lock"] = asyncio.Lock()

        app_state["step"] = "navigating_to_perplexity"
        print("[Startup] Navigating to https://www.perplexity.ai/ ...")
        await page.goto("https://www.perplexity.ai/", wait_until="domcontentloaded", timeout=60000)

        app_state["step"] = "waiting_for_ask_input"
        print(f"[Startup] Loaded page title: {await page.title()}, URL: {page.url}")

        await wait_for_perplexity_input(page, timeout=90000)

        app_state["step"] = "ready"
        app_state["initialized"] = True
        app_state["init_error"] = None
        print("[Startup] Perplexity API Service is ready and authenticated!")

    except Exception as e:
        app_state["init_error"] = str(e)
        app_state["step"] = f"error: {e}"
        print(f"[Error] Browser initialization failed at step {app_state['step']}: {e}")

async def ensure_ready():
    if app_state["initialized"]:
        return

    task = app_state.get("init_task")
    if task and not task.done():
        await task

    if not app_state["initialized"]:
        await initialize_browser()

    if not app_state["initialized"]:
        err = app_state.get("init_error") or f"Service at step '{app_state.get('step')}'"
        raise HTTPException(status_code=503, detail=f"Perplexity service not ready: {err}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state["init_task"] = asyncio.create_task(initialize_browser())
    yield
    if app_state.get("browser"):
        await app_state["browser"].close()
    if app_state.get("playwright"):
        await app_state["playwright"].stop()

app = FastAPI(
    title="Perplexity.ai Unofficial API",
    description="FastAPI service for Perplexity.ai with conversation persistence and new search/chat endpoints.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PromptRequest(BaseModel):
    prompt: str
    chat_id: Optional[str] = None

class NewChatRequest(BaseModel):
    prompt: str

@app.get("/", operation_id="root_get")
@app.head("/", operation_id="root_head")
async def root():
    page = app_state.get("page")
    current_url = page.url if page else None
    return {
        "status": "ready" if app_state["initialized"] else "initializing",
        "current_step": app_state["step"],
        "service": "Perplexity.ai API",
        "init_error": app_state["init_error"],
        "current_chat_url": current_url,
        "current_chat_id": extract_chat_id(current_url) if current_url else None,
        "endpoints": {
            "GET /console": "Interactive Web Console UI",
            "POST /chat": "Continue in the active thread or specific chat_id (retains conversational context)",
            "POST /chat/new": "Open a fresh search session and send the first prompt",
            "GET /chat/status": "Check active conversation ID and URL",
            "GET /debug/screenshot": "View real-time screenshot of the browser window",
        },
    }

@app.get("/console", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
async def serve_console():
    index_file = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Console index.html not found</h1>", status_code=404)

@app.get("/debug/screenshot")
async def debug_screenshot():
    """Returns a real-time PNG screenshot of what the browser currently sees on Render."""
    page = app_state.get("page")
    if not page:
        raise HTTPException(status_code=400, detail="Page not initialized yet")
    screenshot_bytes = await page.screenshot()
    return Response(content=screenshot_bytes, media_type="image/png")

@app.get("/chat/status")
async def chat_status():
    await ensure_ready()
    page = app_state.get("page")
    return {
        "current_url": page.url,
        "current_chat_id": extract_chat_id(page.url),
    }

@app.post("/chat")
async def chat_continue(req: PromptRequest):
    await ensure_ready()
    page = app_state.get("page")
    lock = app_state.get("lock")

    async with lock:
        if req.chat_id:
            target_url = f"https://www.perplexity.ai/search/{req.chat_id}"
            if page.url != target_url:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await wait_for_perplexity_input(page, timeout=30000)

        try:
            result = await send_prompt_on_page(page, req.prompt)
            return result
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/new")
async def chat_new(req: NewChatRequest):
    await ensure_ready()
    page = app_state.get("page")
    lock = app_state.get("lock")

    async with lock:
        await page.goto("https://www.perplexity.ai/", wait_until="domcontentloaded", timeout=60000)
        await wait_for_perplexity_input(page, timeout=30000)

        try:
            result = await send_prompt_on_page(page, req.prompt)
            return result
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)

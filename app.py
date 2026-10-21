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
from fastapi.responses import Response
from pydantic import BaseModel
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

    input_el = page.locator("#ask-input").first
    await input_el.wait_for(state="visible", timeout=30000)
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
            "--disable-gpu",
        ]
        if sys.platform.startswith("win"):
            launch_args.append("--window-position=-20000,-20000")

        print("[Startup] Launching Chromium engine...")
        p = await async_playwright().start()
        browser = await p.chromium.launch(
            headless=False,
            args=launch_args,
        )

        app_state["step"] = "creating_context"
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        )
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

        await page.wait_for_selector("#ask-input", timeout=45000)
        await dismiss_cookie_banner(page)

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

@app.get("/")
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
            "POST /chat": "Continue in the active thread or specific chat_id (retains conversational context)",
            "POST /chat/new": "Open a fresh search session and send the first prompt",
            "GET /chat/status": "Check active conversation ID and URL",
            "GET /debug/screenshot": "View real-time screenshot of the browser window",
        },
    }

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
                await page.wait_for_selector("#ask-input", timeout=30000)

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
        await page.wait_for_selector("#ask-input", timeout=30000)

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

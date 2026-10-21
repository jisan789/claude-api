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
from pydantic import BaseModel
from playwright.async_api import async_playwright

app_state = {
    "playwright": None,
    "browser": None,
    "page": None,
    "lock": None,
    "initialized": False,
    "init_error": None,
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
    match = re.search(r"/chat/([a-f0-9\-]+)", url)
    return match.group(1) if match else None

async def wait_for_response_completion(new_assistant_row) -> str:
    printed_len = 0
    stable_cycles = 0
    full_text = ""
    max_wait = 180
    start_time = time.time()

    while time.time() - start_time < max_wait:
        reply_el = new_assistant_row.locator('[data-perf-reply-text]').or_(
            new_assistant_row.locator('.standard-markdown')
        )

        if await reply_el.count() > 0:
            current_text = await reply_el.inner_text()
            if len(current_text) > printed_len:
                full_text = current_text
                printed_len = len(current_text)
                stable_cycles = 0

        streaming_attr = await new_assistant_row.get_attribute("data-perf-row-streaming")
        has_copy_btn = (await new_assistant_row.locator('button[aria-label="Copy"]').count()) > 0

        if streaming_attr == "false" and has_copy_btn and printed_len > 0:
            stable_cycles += 1
            if stable_cycles >= 2:
                return full_text
        else:
            stable_cycles = 0

        await asyncio.sleep(0.4)

    if full_text:
        return full_text
    raise TimeoutError("Timed out waiting for Claude response to complete.")

async def send_prompt_on_page(page, prompt_text: str) -> dict:
    prior_count = await page.locator('div[data-perf-row="assistant"]').count()

    input_el = page.locator('[data-testid="chat-input"]').first
    await input_el.wait_for(state="visible", timeout=30000)
    await input_el.click()
    await page.keyboard.type(prompt_text, delay=15)
    await asyncio.sleep(0.3)

    send_btn = page.locator('button[data-testid="chat-input-send"]')
    await send_btn.wait_for(state="visible", timeout=10000)
    await send_btn.click()

    wait_start = time.time()
    while await page.locator('div[data-perf-row="assistant"]').count() <= prior_count:
        if time.time() - wait_start > 30:
            raise TimeoutError("Claude did not begin generating a response within 30 seconds.")
        await asyncio.sleep(0.3)

    new_row = page.locator('div[data-perf-row="assistant"]').nth(prior_count)
    reply_text = await wait_for_response_completion(new_row)

    current_url = page.url
    chat_id = extract_chat_id(current_url)

    return {
        "response": reply_text,
        "chat_id": chat_id,
        "chat_url": current_url,
    }

async def initialize_browser():
    """Initializes the browser and loads cookies if not already done."""
    if app_state["initialized"]:
        return

    # Check for cookies in ENV or file
    raw_cookies_env = os.getenv("CLAUDE_COOKIES")
    if raw_cookies_env:
        try:
            raw_cookies = json.loads(raw_cookies_env)
        except Exception as e:
            app_state["init_error"] = f"Failed to parse CLAUDE_COOKIES JSON: {e}"
            raise RuntimeError(app_state["init_error"])
    else:
        cookie_file = os.path.join(os.path.dirname(__file__), "cookies.json")
        if os.path.exists(cookie_file):
            with open(cookie_file, "r", encoding="utf-8") as f:
                raw_cookies = json.load(f)
        else:
            app_state["init_error"] = "CLAUDE_COOKIES environment variable is not set. Please set it in your Render Dashboard."
            raise RuntimeError(app_state["init_error"])

    cookies = [format_cookie(c) for c in raw_cookies]

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    if sys.platform.startswith("win"):
        launch_args.append("--window-position=-20000,-20000")

    print("Launching Chromium engine...")
    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False,
        args=launch_args,
    )

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    )
    await context.add_cookies(cookies)

    page = await context.new_page()
    print("Navigating to https://claude.ai/new ...")
    await page.goto("https://claude.ai/new", wait_until="domcontentloaded", timeout=60000)
    await page.locator('[data-testid="chat-input"]').first.wait_for(state="visible", timeout=45000)
    print("Claude API Service is live and authenticated!")

    app_state["playwright"] = p
    app_state["browser"] = browser
    app_state["page"] = page
    app_state["lock"] = asyncio.Lock()
    app_state["initialized"] = True
    app_state["init_error"] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Try to initialize immediately on startup if cookies are present
    try:
        await initialize_browser()
    except Exception as e:
        print(f"Warning during startup initialization: {e}")
        print("Service will start and wait for CLAUDE_COOKIES environment variable.")

    yield

    if app_state["browser"]:
        print("Shutting down Chromium...")
        await app_state["browser"].close()
    if app_state["playwright"]:
        await app_state["playwright"].stop()

app = FastAPI(
    title="Claude.ai Unofficial API",
    description="FastAPI service for Claude.ai with conversation persistence and new chat endpoints.",
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
        "status": "ready" if app_state["initialized"] else "waiting_for_configuration",
        "init_error": app_state["init_error"],
        "service": "Claude.ai API",
        "current_chat_url": current_url,
        "current_chat_id": extract_chat_id(current_url) if current_url else None,
        "endpoints": {
            "POST /chat": "Continue in the same active chat (maintains conversation context)",
            "POST /chat/new": "Open a fresh new chat session and send the first prompt",
            "GET /chat/status": "Check active conversation ID and status",
        },
    }

@app.get("/chat/status")
async def chat_status():
    if not app_state["initialized"]:
        # Try initializing now in case env var was set
        try:
            await initialize_browser()
        except Exception:
            pass

    if not app_state["initialized"]:
        raise HTTPException(
            status_code=503,
            detail=f"Service not initialized: {app_state['init_error'] or 'Missing CLAUDE_COOKIES'}"
        )

    page = app_state.get("page")
    return {
        "current_url": page.url,
        "current_chat_id": extract_chat_id(page.url),
    }

@app.post("/chat")
async def chat_continue(req: PromptRequest):
    if not app_state["initialized"]:
        try:
            await initialize_browser()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize Claude session: {e}. Please ensure CLAUDE_COOKIES is set in Render Environment Variables."
            )

    page = app_state.get("page")
    lock = app_state.get("lock")

    async with lock:
        if req.chat_id:
            target_url = f"https://claude.ai/chat/{req.chat_id}"
            if page.url != target_url:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                await page.locator('[data-testid="chat-input"]').first.wait_for(state="visible", timeout=30000)

        try:
            result = await send_prompt_on_page(page, req.prompt)
            return result
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/chat/new")
async def chat_new(req: NewChatRequest):
    if not app_state["initialized"]:
        try:
            await initialize_browser()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize Claude session: {e}. Please ensure CLAUDE_COOKIES is set in Render Environment Variables."
            )

    page = app_state.get("page")
    lock = app_state.get("lock")

    async with lock:
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded", timeout=60000)
        await page.locator('[data-testid="chat-input"]').first.wait_for(state="visible", timeout=30000)

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

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

app_state = {}

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
    """
    Waits for the new assistant row to finish streaming completely
    and returns the full text.
    """
    printed_len = 0
    stable_cycles = 0
    full_text = ""
    max_wait = 180  # Max 3 minutes
    start_time = time.time()

    while time.time() - start_time < max_wait:
        # Locate text element inside assistant row
        reply_el = new_assistant_row.locator('[data-perf-reply-text]').or_(
            new_assistant_row.locator('.standard-markdown')
        )

        if await reply_el.count() > 0:
            current_text = await reply_el.inner_text()
            if len(current_text) > printed_len:
                full_text = current_text
                printed_len = len(current_text)
                stable_cycles = 0

        # Check completion flags on this assistant row
        streaming_attr = await new_assistant_row.get_attribute("data-perf-row-streaming")
        has_copy_btn = (await new_assistant_row.locator('button[aria-label="Copy"]').count()) > 0

        # Finished when streaming is false, action bar has rendered, and text is non-empty
        if streaming_attr == "false" and has_copy_btn and printed_len > 0:
            stable_cycles += 1
            if stable_cycles >= 2:  # Confirmed stable across checks
                return full_text
        else:
            stable_cycles = 0

        await asyncio.sleep(0.4)

    if full_text:
        return full_text
    raise TimeoutError("Timed out waiting for Claude to complete generating response.")

async def send_prompt_on_page(page, prompt_text: str) -> dict:
    """
    Types prompt into the chat input, clicks send, waits for completion,
    and returns response text along with chat details.
    """
    # 1. Count assistant rows before submission
    prior_count = await page.locator('div[data-perf-row="assistant"]').count()

    # 2. Focus and enter prompt into chat input
    input_el = page.locator('[data-testid="chat-input"]').first
    await input_el.wait_for(state="visible", timeout=30000)
    await input_el.click()
    await page.keyboard.type(prompt_text, delay=15)
    await asyncio.sleep(0.3)

    # 3. Submit
    send_btn = page.locator('button[data-testid="chat-input-send"]')
    await send_btn.wait_for(state="visible", timeout=10000)
    await send_btn.click()

    # 4. Wait for the new assistant row
    wait_start = time.time()
    while await page.locator('div[data-perf-row="assistant"]').count() <= prior_count:
        if time.time() - wait_start > 30:
            raise TimeoutError("Claude did not begin generating a response within 30 seconds.")
        await asyncio.sleep(0.3)

    new_row = page.locator('div[data-perf-row="assistant"]').nth(prior_count)

    # 5. Wait for streaming to finish
    reply_text = await wait_for_response_completion(new_row)

    # 6. Extract chat ID & URL
    current_url = page.url
    chat_id = extract_chat_id(current_url)

    return {
        "response": reply_text,
        "chat_id": chat_id,
        "chat_url": current_url,
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Load cookies (from ENV var or cookies.json)
    raw_cookies_env = os.getenv("CLAUDE_COOKIES")
    if raw_cookies_env:
        raw_cookies = json.loads(raw_cookies_env)
    else:
        cookie_file = os.path.join(os.path.dirname(__file__), "cookies.json")
        if not os.path.exists(cookie_file):
            raise RuntimeError(f"Neither CLAUDE_COOKIES env var nor {cookie_file} found.")
        with open(cookie_file, "r", encoding="utf-8") as f:
            raw_cookies = json.load(f)

    cookies = [format_cookie(c) for c in raw_cookies]

    # 2. Launch Chromium (runs offscreen on Windows/Mac, runs in Xvfb on Linux)
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-setuid-sandbox",
    ]
    # If not on Linux with Xvfb, keep window offscreen
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

    # Verify chat input is ready
    await page.locator('[data-testid="chat-input"]').first.wait_for(state="visible", timeout=45000)
    print("Claude API Service is live and authenticated!")

    app_state["playwright"] = p
    app_state["browser"] = browser
    app_state["page"] = page
    app_state["lock"] = asyncio.Lock()

    yield

    print("Shutting down Chromium...")
    await browser.close()
    await p.stop()

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
        "status": "online",
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
    page = app_state.get("page")
    if not page:
        raise HTTPException(status_code=503, detail="Browser session not initialized")
    return {
        "current_url": page.url,
        "current_chat_id": extract_chat_id(page.url),
    }

@app.post("/chat")
async def chat_continue(req: PromptRequest):
    """
    Continues conversation in the SAME chat so Claude remembers history.
    Optionally pass 'chat_id' to jump to a specific existing conversation.
    """
    page = app_state.get("page")
    lock = app_state.get("lock")
    if not page or not lock:
        raise HTTPException(status_code=503, detail="Browser not ready")

    async with lock:
        # If a specific chat_id was requested and we are not on it, navigate there
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
    """
    Opens a FRESH NEW CHAT session, sends prompt, and returns the response
    along with the newly generated chat_id and chat_url.
    """
    page = app_state.get("page")
    lock = app_state.get("lock")
    if not page or not lock:
        raise HTTPException(status_code=503, detail="Browser not ready")

    async with lock:
        # Navigate to /new to begin a fresh conversation
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

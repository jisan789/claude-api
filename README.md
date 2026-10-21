# Perplexity.ai FastAPI Service

An unofficial API server for Perplexity.ai powered by Playwright and FastAPI. Designed to run locally or deployed to **Render** with Docker.

---

## Features

- **Automated Authentication**: Seamless cookie injection into Chromium using `cookies.json` or environment variables.
- **Persistent Sessions**: Maintain ongoing threads (`POST /chat`) or start fresh searches (`POST /chat/new`).
- **Clean Markdown Output**: Automatically strips internal UI citations to give clean, readable text.
- **FastAPI Endpoints**: Full OpenAPI / Swagger documentation out of the box.
- **Render Ready**: Includes Dockerfile, Xvfb virtual display setup, and `render.yaml`.

---

## API Endpoints

### 1. Open a Fresh New Search
Starts a brand new search session on `https://www.perplexity.ai/` and sends the initial prompt.

- **URL**: `POST /chat/new`
- **Body**:
  ```json
  {
    "prompt": "Explain the concept of quantum entanglement in simple terms."
  }
  ```
- **Response**:
  ```json
  {
    "response": "Quantum entanglement is a phenomenon where two or more particles become connected...",
    "chat_id": "c64332d4-043c-4761-92ed-114a8f33656b",
    "chat_url": "https://www.perplexity.ai/search/c64332d4-043c-4761-92ed-114a8f33656b"
  }
  ```

---

### 2. Continue in the Same Thread (Conversational Memory)
Sends a follow-up query in the active thread so Perplexity retains the full conversational context.

- **URL**: `POST /chat`
- **Body**:
  ```json
  {
    "prompt": "Can you give a real-world example of that?"
  }
  ```
- **Response**:
  ```json
  {
    "response": "A common analogy is a pair of shoes...",
    "chat_id": "c64332d4-043c-4761-92ed-114a8f33656b",
    "chat_url": "https://www.perplexity.ai/search/c64332d4-043c-4761-92ed-114a8f33656b"
  }
  ```
*(Optional: You can pass `"chat_id": "<uuid>"` in the body to jump directly to any previous search).*

---

### 3. Check Chat Status
Returns the active search session URL and ID.

- **URL**: `GET /chat/status`
- **Response**:
  ```json
  {
    "current_url": "https://www.perplexity.ai/search/c64332d4-043c-4761-92ed-114a8f33656b",
    "current_chat_id": "c64332d4-043c-4761-92ed-114a8f33656b"
  }
  ```

---

### 4. Real-Time Debug Screenshot
Inspect the current Chromium browser screen directly from your browser.

- **URL**: `GET /debug/screenshot`

---

## Running Locally

### CLI Mode (Interactive Terminal)
```powershell
python perplexity_chat.py
```
Or send a single prompt directly:
```powershell
python perplexity_chat.py "What is the speed of light?"
```
*(Add `--visible` to watch the browser in action).*

### API Server Mode
```powershell
python -m uvicorn app:app --port 8000
```
Then visit [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive Swagger documentation.

---

## Deploying to Render

1. Push this folder to your GitHub repository (`.gitignore` protects your `cookies.json`).
2. Go to [dashboard.render.com](https://dashboard.render.com) -> **New Web Service**.
3. Select your repository.
4. Choose **Docker** as the Runtime.
5. In **Environment Variables**, add:
   - **Key**: `PERPLEXITY_COOKIES`
   - **Value**: *(Paste the raw JSON content of your cookies from `cookies.json`)*
6. Click **Deploy Web Service**!

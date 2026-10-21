# Claude.ai FastAPI Service

An unofficial API server for Claude.ai powered by Playwright and FastAPI. Designed to run locally or deployed to **Render** with Docker.

---

## API Endpoints

### 1. Open a Fresh New Chat
Starts a brand new chat session on `https://claude.ai/new` and sends the first prompt.

- **URL**: `POST /chat/new`
- **Body**:
  ```json
  {
    "prompt": "Hello Claude! My secret code is 8844."
  }
  ```
- **Response**:
  ```json
  {
    "response": "Hello! I have noted your secret code: 8844.",
    "chat_id": "4f4d8d69-ab29-4976-945f-2a5c73a98804",
    "chat_url": "https://claude.ai/chat/4f4d8d69-ab29-4976-945f-2a5c73a98804"
  }
  ```

---

### 2. Continue in the Same Chat (Memory Retention)
Sends a follow-up message in the active chat so Claude remembers full conversation history.

- **URL**: `POST /chat`
- **Body**:
  ```json
  {
    "prompt": "What was the secret code I gave you?"
  }
  ```
- **Response**:
  ```json
  {
    "response": "The secret code you gave me was 8844.",
    "chat_id": "4f4d8d69-ab29-4976-945f-2a5c73a98804",
    "chat_url": "https://claude.ai/chat/4f4d8d69-ab29-4976-945f-2a5c73a98804"
  }
  ```
*(Optional: You can pass `"chat_id": "<uuid>"` in the body to jump directly to any previous chat).*

---

### 3. Check Chat Status
Returns the active chat session URL and ID.

- **URL**: `GET /chat/status`
- **Response**:
  ```json
  {
    "current_url": "https://claude.ai/chat/4f4d8d69-ab29-4976-945f-2a5c73a98804",
    "current_chat_id": "4f4d8d69-ab29-4976-945f-2a5c73a98804"
  }
  ```

---

## Running Locally

```powershell
python -m uvicorn app:app --port 8000
```
Then visit [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for the interactive Swagger documentation.

---

## Deploying to Render

1. Push this folder to a GitHub repository (`.gitignore` protects your `cookies.json`).
2. Go to [dashboard.render.com](https://dashboard.render.com) -> **New Web Service**.
3. Select your repository.
4. Choose **Docker** as the Runtime.
5. In **Environment Variables**, add:
   - **Key**: `CLAUDE_COOKIES`
   - **Value**: *(Paste the raw JSON content of your cookies)*
6. Click **Deploy Web Service**!

FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Install Xvfb for virtual display
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set default port
ENV PORT=8000
EXPOSE 8000

# Start FastAPI server inside Xvfb virtual screen
CMD xvfb-run --server-args="-screen 0 1280x800x24 -ac" uvicorn app:app --host 0.0.0.0 --port $PORT

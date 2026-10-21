FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Install Xvfb and X11 utilities
RUN apt-get update && apt-get install -y \
    xvfb \
    x11-utils \
    xauth \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure start.sh has unix line endings and execute permissions
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

ENV PORT=8000
EXPOSE 8000

CMD ["./start.sh"]

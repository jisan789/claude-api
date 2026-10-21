FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

RUN apt-get update && apt-get install -y \
    xvfb \
    x11-utils \
    xauth \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

EXPOSE 10000

CMD ["./start.sh"]

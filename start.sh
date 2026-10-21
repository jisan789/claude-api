#!/bin/bash
set -e

# Ensure X11 directory exists
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

# Clean up any previous lock files
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

# Start Xvfb virtual display in the background on :99
echo "Starting Xvfb virtual display on :99..."
Xvfb :99 -screen 0 1280x800x24 -ac -nolisten tcp &
export DISPLAY=:99

SERVER_PORT="${PORT:-10000}"
echo "Binding Uvicorn to 0.0.0.0:${SERVER_PORT}..."

# Exec directly into uvicorn so signals pass through cleanly
exec uvicorn app:app --host 0.0.0.0 --port "${SERVER_PORT}"

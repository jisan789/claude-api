#!/bin/bash
set -e

# Ensure X11 socket directory exists
mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

# Clean up any stale X locks
rm -f /tmp/.X*-lock /tmp/.X11-unix/* 2>/dev/null || true

# Determine port (Render supplies $PORT, default to 10000)
SERVER_PORT="${PORT:-10000}"
echo "Starting server on port ${SERVER_PORT}..."

# Start with Xvfb virtual display
exec xvfb-run -a --server-args="-screen 0 1280x800x24 -ac -nolisten tcp -dpi 96 +extension RANDR" uvicorn app:app --host 0.0.0.0 --port "${SERVER_PORT}"

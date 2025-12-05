#!/bin/bash
# Startup script for Railway deployment
# Uses Railway's PORT environment variable or defaults to 8000

PORT=${PORT:-8000}
echo "Starting uvicorn on port $PORT"
exec uvicorn app:app --host 0.0.0.0 --port "$PORT"

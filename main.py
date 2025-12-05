#!/usr/bin/env python3
"""
Main entry point - just exec uvicorn command line
"""
import os
import sys

# Get PORT from environment
port = os.getenv("PORT", "8000")
print(f"🚀 Starting on port {port}")
sys.stdout.flush()

# Exec uvicorn (replaces this process)
os.execlp("uvicorn", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", port)

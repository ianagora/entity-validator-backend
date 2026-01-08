#!/usr/bin/env python3
"""
Main entry point - just exec uvicorn command line
"""
import os
import sys

# Get PORT from environment
port = os.getenv("PORT", "8000")
print(f"🚀 Starting on port {port}")

# DEBUG: Print all environment variables related to our app
print("=" * 60)
print("DEBUG: Environment Variables Check")
print("=" * 60)
print(f"CH_API_KEY present: {bool(os.getenv('CH_API_KEY'))}")
print(f"CH_API_KEY value: {os.getenv('CH_API_KEY', 'NOT SET')[:20]}...")  # First 20 chars only
print(f"OPENAI_API_KEY present: {bool(os.getenv('OPENAI_API_KEY'))}")
print(f"ENVIRONMENT: {os.getenv('ENVIRONMENT', 'NOT SET')}")
print(f"PORT: {port}")
print("=" * 60)
sys.stdout.flush()

# Exec uvicorn (replaces this process)
os.execlp("uvicorn", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", port)

#!/usr/bin/env python3
"""
Railway startup script that properly handles PORT environment variable
"""
import os
import sys
import subprocess

# Get PORT from environment, default to 8000
port = os.getenv('PORT', '8000')

print(f"Starting uvicorn on port {port}")
sys.stdout.flush()

# Start uvicorn
subprocess.run([
    'uvicorn',
    'app:app',
    '--host', '0.0.0.0',
    '--port', port
])

#!/usr/bin/env python3
"""
Main entry point for Railway deployment
Handles PORT environment variable and starts uvicorn
"""
import os
import uvicorn

if __name__ == "__main__":
    # Get PORT from environment, default to 8000
    port = int(os.getenv("PORT", "8000"))
    
    print(f"🚀 Starting Entity Validator Backend on port {port}")
    
    # Start uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )

#!/usr/bin/env python3
"""
Minimal app version to test if basic FastAPI works on Railway.
"""
import os
from fastapi import FastAPI

app = FastAPI(title="Minimal Test")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Minimal app is working"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "minimal-test"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

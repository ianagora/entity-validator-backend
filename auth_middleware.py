"""
API Authentication Middleware for Railway Backend
Protects endpoints with Bearer token authentication
"""

import os
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer()

BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")

if not BACKEND_API_KEY:
    print("WARNING: BACKEND_API_KEY not set! API will be unprotected!")
    BACKEND_API_KEY = "development-only-key-change-in-production"

def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    """
    Verify the API key from the Authorization header.
    
    Usage in routes:
        @app.get("/protected", dependencies=[Depends(verify_api_key)])
        def protected_route():
            return {"message": "Access granted"}
    
    Or inject into function:
        @app.get("/protected")
        def protected_route(token: str = Depends(verify_api_key)):
            return {"message": f"Access granted with token: {token}"}
    """
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Use 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if credentials.credentials != BACKEND_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return credentials.credentials


def verify_api_key_optional(credentials: HTTPAuthorizationCredentials = Security(security, auto_error=False)) -> bool:
    """
    Optional API key verification (doesn't raise exception if missing).
    Returns True if valid key provided, False otherwise.
    
    Useful for endpoints that work better with auth but don't require it.
    """
    if not credentials:
        return False
    
    if credentials.scheme.lower() != "bearer":
        return False
    
    return credentials.credentials == BACKEND_API_KEY

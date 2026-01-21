# security_patch.py - Security Hardening Integration Script
# This script patches app.py to add CREST-compliant security controls

import re

def apply_security_patches(app_py_content: str) -> str:
    """Apply all security patches to app.py"""
    
    # 1. Add middleware imports after existing imports
    import_patch = """
# CREST Security Middleware (Added by security hardening)
from security_middleware import (
    SecurityHeadersMiddleware,
    AuthEnforcementMiddleware,
    CSRFMiddleware,
    csrf_protection,
    get_real_ip
)
"""
    
    # Find the line with "from security import" and add our imports after
    content = re.sub(
        r'(from security import[\s\S]*?\))',
        r'\1\n' + import_patch,
        app_py_content,
        count=1
    )
    
    # 2. Update rate limiter to use real IP from Cloudflare
    rate_limiter_patch = """
# Initialize rate limiter with Cloudflare IP support
def get_client_ip(request: Request) -> str:
    \"\"\"Get real client IP, prioritizing Cloudflare headers.\"\"\"
    return get_real_ip(request)

limiter = Limiter(key_func=get_client_ip)
"""
    
    content = re.sub(
        r'# Initialize rate limiter\nlimiter = Limiter\(key_func=get_remote_address\)',
        rate_limiter_patch,
        content
    )
    
    # 3. Add security middleware after CORS middleware
    middleware_patch = """
# CREST Security Middleware Stack
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthEnforcementMiddleware)
app.add_middleware(CSRFMiddleware)
"""
    
    # Add after CORS middleware
    content = re.sub(
        r'(app\.add_middleware\(\s*CORSMiddleware,[\s\S]*?\))',
        r'\1\n\n' + middleware_patch,
        content,
        count=1
    )
    
    # 4. Update login endpoint to set CSRF token and use cookies
    login_cookie_patch = """
    # Generate CSRF token for subsequent requests
    csrf_token = csrf_protection.generate_csrf_token()
    
    # Create response with tokens in HttpOnly cookies
    response = JSONResponse(content={
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name", "")
        }
    })
    
    # Set auth cookies (HttpOnly, Secure in production)
    is_production = os.getenv("ENVIRONMENT", "development").lower() == "production"
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    )
    
    # Set CSRF token
    csrf_protection.set_csrf_cookie(response, csrf_token)
    
    # Log successful login
    log_audit_event(
        action="login_success",
        status="success",
        user_id=user["id"],
        user_email=user["email"],
        ip_address=get_real_ip(request),
        user_agent=request.headers.get("user-agent")
    )
    
    return response
"""
    
    # Replace the return statement in login endpoint
    content = re.sub(
        r'return \{\s*"access_token": access_token,\s*"refresh_token": refresh_token,\s*"token_type": "bearer"\s*\}',
        login_cookie_patch,
        content
    )
    
    # 5. Fix /health endpoint to accept GET (currently returns 405)
    health_fix = """
@app.get("/health")
@app.head("/health")
async def health_check():
    \"\"\"Health check endpoint for monitoring.\"\"\"
    return {
        "status": "healthy",
        "version": os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown")[:8],
        "environment": os.getenv("ENVIRONMENT", "development"),
        "timestamp": datetime.utcnow().isoformat()
    }
"""
    
    # Add health endpoint if not present as GET
    if '@app.get("/health")' not in content:
        # Find where to add it (after app initialization)
        content = re.sub(
            r'(app\.mount\("/static".*?\n)',
            r'\1\n' + health_fix + '\n',
            content,
            count=1
        )
    
    # 6. Update audit logging to use real IP
    content = re.sub(
        r'ip_address=request\.client\.host',
        r'ip_address=get_real_ip(request)',
        content
    )
    
    return content


if __name__ == "__main__":
    print("Security patch script ready. Use this to patch app.py programmatically.")

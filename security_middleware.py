# security_middleware.py - CREST Security Hardening Middleware

import os
import secrets
from datetime import datetime
from typing import Optional, Callable
from fastapi import Request, Response, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import MutableHeaders
import logging

logger = logging.getLogger(__name__)

# ==============================================================================
# CSRF PROTECTION
# ==============================================================================

class CSRFProtection:
    """Double-submit cookie CSRF protection for state-changing requests."""
    
    def __init__(self):
        self.csrf_cookie_name = "csrf_token"
        self.csrf_header_name = "X-CSRF-Token"
        self.csrf_form_field = "csrf_token"
        
    def generate_csrf_token(self) -> str:
        """Generate a secure CSRF token."""
        return secrets.token_urlsafe(32)
    
    def set_csrf_cookie(self, response: Response, token: str):
        """Set CSRF token as a cookie."""
        is_production = os.getenv("ENVIRONMENT", "development").lower() == "production"
        response.set_cookie(
            key=self.csrf_cookie_name,
            value=token,
            httponly=False,  # JS needs to read this for header
            secure=is_production,
            samesite="lax",
            max_age=3600 * 8  # 8 hours
        )
    
    async def validate_csrf(self, request: Request) -> bool:
        """Validate CSRF token from request."""
        # Skip CSRF for safe methods
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
            
        # Skip CSRF for API calls with Bearer auth (stateless)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return True
        
        # Get CSRF token from cookie
        csrf_cookie = request.cookies.get(self.csrf_cookie_name)
        if not csrf_cookie:
            return False
        
        # Get CSRF token from header or form data
        csrf_token = request.headers.get(self.csrf_header_name)
        
        if not csrf_token:
            # Try to get from form data
            if request.method == "POST":
                try:
                    form_data = await request.form()
                    csrf_token = form_data.get(self.csrf_form_field)
                except:
                    pass
        
        # Validate tokens match
        return csrf_cookie == csrf_token and csrf_token is not None


csrf_protection = CSRFProtection()


# ==============================================================================
# SECURITY HEADERS MIDDLEWARE
# ==============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Get environment
        is_production = os.getenv("ENVIRONMENT", "development").lower() == "production"
        
        # Content Security Policy
        if is_production:
            csp = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "img-src 'self' data: https:; "
                "font-src 'self' data: https://cdn.jsdelivr.net; "
                "connect-src 'self' https://api.company-information.service.gov.uk; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self';"
            )
        else:
            # Development mode - more permissive
            csp = (
                "default-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://api.company-information.service.gov.uk;"
            )
        
        # Set security headers
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        
        # HSTS only in production
        if is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        
        return response


# ==============================================================================
# AUTH ENFORCEMENT MIDDLEWARE
# ==============================================================================

class AuthEnforcementMiddleware(BaseHTTPMiddleware):
    """Enforce authentication on API routes."""
    
    # Routes that don't require authentication
    PUBLIC_ROUTES = {
        "/health",
        "/",
        "/login",
        "/token",
        "/api/health",
        "/api/admin/force-init",
        "/api/admin/force-init-api-keys",  # Force user init for first-time setup
        "/docs",
        "/redoc",
        "/openapi.json"
    }
    
    # Prefixes that don't require auth
    PUBLIC_PREFIXES = [
        "/static/",
        "/favicon.ico"
    ]
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        
        # Check if route is public
        if path in self.PUBLIC_ROUTES:
            return await call_next(request)
        
        # Check if route has public prefix
        if any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES):
            return await call_next(request)
        
        # Enforce auth on /api/* routes (except /api/health)
        if path.startswith("/api/") and path != "/api/health":
            # Check for authentication (API key, Bearer token, or cookie)
            has_auth = False
            auth_method = None
            
            # Priority 1: Check API Key (X-API-Key header)
            api_key_header = request.headers.get("X-API-Key", "")
            if api_key_header:
                # Import verify_api_key here to avoid circular imports
                try:
                    from api_key_management import verify_api_key, log_api_key_usage
                    key_info = verify_api_key(api_key_header)
                    if key_info:
                        has_auth = True
                        auth_method = "api_key"
                        # Log usage for auditing
                        log_api_key_usage(
                            key_id=key_info['key_id'],
                            endpoint=path,
                            method=request.method,
                            ip_address=request.headers.get("CF-Connecting-IP") or request.client.host,
                            user_agent=request.headers.get("User-Agent")
                        )
                except Exception as e:
                    logger.warning(f"API key verification failed: {e}")
            
            # Priority 2: Check Bearer token
            if not has_auth:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    has_auth = True
                    auth_method = "bearer_token"
            
            # Priority 3: Check auth cookie
            if not has_auth:
                if "access_token" in request.cookies or "session" in request.cookies:
                    has_auth = True
                    auth_method = "cookie"
            
            if not has_auth:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Authentication required"},
                    headers={"WWW-Authenticate": "Bearer"}
                )
        
        return await call_next(request)


# ==============================================================================
# CSRF VALIDATION MIDDLEWARE
# ==============================================================================

class CSRFMiddleware(BaseHTTPMiddleware):
    """Validate CSRF tokens for state-changing requests."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip CSRF for safe methods and public routes
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return await call_next(request)
        
        # Skip for health/docs and auth endpoints
        if request.url.path in ["/health", "/api/health", "/docs", "/redoc", "/openapi.json", 
                                "/auth/login", "/auth/refresh", "/api/admin/force-init", "/api/admin/force-init-api-keys"]:
            return await call_next(request)
        
        # Skip for Bearer auth (stateless API) - includes frontend API calls
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return await call_next(request)
        
        # Skip for API key auth (X-API-Key header for frontend)
        if request.headers.get("X-API-Key"):
            return await call_next(request)
        
        # Validate CSRF
        is_valid = await csrf_protection.validate_csrf(request)
        if not is_valid:
            # Log CSRF failure
            logger.warning(f"CSRF validation failed for {request.method} {request.url.path} from {request.client.host}")
            
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "CSRF validation failed"}
            )
        
        return await call_next(request)


# ==============================================================================
# REAL IP EXTRACTION (Cloudflare)
# ==============================================================================

def get_real_ip(request: Request) -> str:
    """Extract real client IP from Cloudflare headers or fallback."""
    # Cloudflare headers (in order of preference)
    cf_connecting_ip = request.headers.get("CF-Connecting-IP")
    if cf_connecting_ip:
        return cf_connecting_ip
    
    true_client_ip = request.headers.get("True-Client-IP")
    if true_client_ip:
        return true_client_ip
    
    # Fallback to X-Forwarded-For
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # Get first IP in chain
        return x_forwarded_for.split(",")[0].strip()
    
    # Final fallback
    return request.client.host if request.client else "unknown"

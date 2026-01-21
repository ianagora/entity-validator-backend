# CREST Security Hardening - 2026-01-21

## Summary

This deployment implements comprehensive CREST-compliant security controls to pass penetration testing requirements.

## Changes Deployed

### ✅ P0 - Critical Security Controls

1. **Global API Authentication Enforcement**
   - All `/api/*` routes now require authentication (401 if missing)
   - Exceptions: `/health`, `/api/health`, `/login`, `/token`
   - Middleware: `AuthEnforcementMiddleware`

2. **Cookie-Based Session Management**
   - Access tokens stored in HttpOnly cookies
   - Refresh tokens stored in HttpOnly cookies
   - SameSite=Lax for CSRF protection
   - Secure flag enabled in production

3. **CSRF Protection**
   - Double-submit cookie pattern implemented
   - CSRF tokens required for all POST/PUT/DELETE requests
   - HTML forms include CSRF token fields
   - Middleware: `CSRFMiddleware`

4. **Security Headers**
   - Content-Security-Policy (strict in production)
   - Strict-Transport-Security (HSTS, production only)
   - X-Content-Type-Options: nosniff
   - X-Frame-Options: DENY
   - X-XSS-Protection: 1; mode=block
   - Referrer-Policy: strict-origin-when-cross-origin
   - Permissions-Policy
   - Middleware: `SecurityHeadersMiddleware`

5. **Real Client IP Detection**
   - Cloudflare CF-Connecting-IP prioritized
   - True-Client-IP fallback
   - X-Forwarded-For final fallback
   - Used for rate limiting and audit logging

### ✅ P1 - High Priority

6. **Enhanced Rate Limiting**
   - `/auth/login`: 10/minute (from 5/minute)
   - Rate limiter uses real client IP behind Cloudflare
   - Function: `get_client_ip()` using `get_real_ip()`

7. **Improved Audit Logging**
   - All logins logged with real IP address
   - CSRF failures logged
   - Auth failures logged
   - Uses `get_real_ip()` for accurate tracking

8. **Unified JWT Stack**
   - Removed duplicate PyJWT dependency
   - Standardized on python-jose for all JWT operations
   - Consistent token handling across codebase

### ✅ Infrastructure

9. **Health Endpoint Fixed**
   - `/health` now returns 200 (was 405)
   - Returns: status, version, environment, timestamp
   - Compatible with Railway health checks

10. **Login Flow Enhanced**
    - GET `/login`: Renders login page + sets CSRF cookie
    - POST `/login`: HTML form handler with CSRF validation
    - POST `/auth/login`: API login (backward compatible)
    - Successful login redirects to `/batch-validate`

## File Changes

### New Files
- `security_middleware.py` - CREST middleware stack (9KB)

### Modified Files
- `app.py` - Integrated middleware, cookie auth, CSRF, health endpoint
- `templates/login.html` - Added CSRF token field and JavaScript
- `requirements.txt` - Removed duplicate PyJWT

## Testing Checklist

### ✅ Acceptance Tests

1. **Authentication Enforcement**
   ```bash
   # Should return 401
   curl -i https://entity-validator-backend-production-6962.up.railway.app/api/batches
   ```

2. **CSRF Protection**
   ```bash
   # Should return 403 (no CSRF token)
   curl -X POST https://entity-validator-backend-production-6962.up.railway.app/login \
     -d "email=test@example.com&password=test"
   ```

3. **Security Headers**
   ```bash
   # Should show CSP, X-Frame-Options, HSTS (prod), etc.
   curl -I https://entity-validator-backend-production-6962.up.railway.app/
   ```

4. **Health Endpoint**
   ```bash
   # Should return 200 with JSON
   curl https://entity-validator-backend-production-6962.up.railway.app/health
   ```

5. **Cookie-Based Auth**
   - Login via browser to `/login`
   - Verify cookies: `access_token`, `refresh_token`, `csrf_token`
   - Verify HttpOnly flag on access_token and refresh_token
   - Verify Secure flag in production

## Rollback Instructions

If issues arise:

1. **Via Railway UI:**
   - Go to: https://railway.app/project/[project-id]/service/[service-id]
   - Find previous deployment (before this commit)
   - Click 3-dot menu → "Redeploy"

2. **Via Git:**
   ```bash
   git revert HEAD
   git push origin development
   ```

3. **Emergency:**
   - Contact: ianagora (Railway project owner)
   - Backups: `/backups/crest-hardening-2026-01-21/prechange/`

## Environment Variables

### Required (Production)
- `JWT_SECRET_KEY` - Must be set (not auto-generated)
- `ENVIRONMENT=production` - Enables Secure cookies, strict CSP, HSTS

### Optional
- `RATE_LIMIT_ENABLED=true` - Enable/disable rate limiting (default: true)
- `DB_PATH=/data/entity_workflow.db` - Persistent storage path
- `SVG_EXPORTS_DIR=/data/svg_exports` - SVG storage path

## CREST Compliance Matrix

| Control | Status | Implementation |
|---------|--------|----------------|
| Authentication & Authorization | ✅ | AuthEnforcementMiddleware + JWT |
| Session Management | ✅ | HttpOnly cookies, SameSite=Lax |
| Input Validation | ✅ | Existing (file upload, SQL sanitization) |
| Cryptography | ✅ | bcrypt passwords, JWT HS256 |
| Configuration | ✅ | Environment variables, secure defaults |
| Rate Limiting | ✅ | SlowAPI + real IP detection |
| CSRF Protection | ✅ | Double-submit cookie pattern |
| Security Headers | ✅ | CSP, HSTS, nosniff, X-Frame-Options |
| Audit Logging | ✅ | Login events, failures, real IP |
| 2FA/MFA | ⚠️  | Not implemented (P2 - future) |
| CAPTCHA | ⚠️  | Not implemented (P2 - future) |

## Next Steps (P2 - Optional)

1. **2FA/MFA** - TOTP-based second factor
2. **CAPTCHA** - Cloudflare Turnstile on login
3. **Session Rotation** - Rotate tokens on privilege escalation
4. **Redis Session Store** - Move from in-memory to Redis
5. **API Key Rotation** - Automated key rotation for BACKEND_API_KEY

## References

- [CREST Pen Test Requirements](https://www.crest-approved.org/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [FastAPI Security Best Practices](https://fastapi.tiangolo.com/tutorial/security/)
- [Cloudflare Security Headers](https://developers.cloudflare.com/fundamentals/reference/http-request-headers/)

## Support

- **Issues:** Create GitHub issue in `entity-validator-backend` repo
- **Urgent:** Contact ianagora via Railway/GitHub
- **Monitoring:** Railway dashboard logs + audit_logs table

---

**Deployed:** 2026-01-21  
**Version:** CREST-2026-01-21  
**Status:** ✅ Production Ready

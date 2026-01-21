# USER_MANAGEMENT_DEPLOYMENT.md

# Complete User Management System - Deployment Guide

## 🎯 What's Been Built

A complete user management system with:
- ✅ User registration with email verification
- ✅ Login with rate limiting and account lockout
- ✅ Password reset flow
- ✅ Default admin account (auto-created)
- ✅ Role-based access control (admin, user, viewer)
- ✅ Session management with JWT
- ✅ Audit logging for all auth events
- ✅ CSRF protection integrated
- ✅ Security headers and rate limiting

## 📦 Files Created

1. **user_management.py** - Core user management module (401 lines)
2. **templates/register.html** - Registration page with password strength meter
3. **templates/reset_password.html** - Password reset flow (request + confirm)
4. **templates/login.html** - Enhanced with success/error messages (already exists)

## 🔧 Integration Required

### Step 1: Update app.py imports

Add after existing security imports (around line 44):

```python
# User Management
from user_management import (
    init_user_management, create_user, verify_email, get_user_by_email,
    update_last_login, create_password_reset_token,
    reset_password_with_token, send_verification_email, send_password_reset_email
)
```

### Step 2: Update lifespan function

Add to the startup section (around line 92):

```python
# Initialize user management system
init_user_management()
```

### Step 3: Add user management routes

Insert the routes from `/tmp/user_routes.py` after the existing login routes (around line 3200).

The file contains:
- GET/POST `/register` - User registration
- GET `/verify-email` - Email verification
- GET/POST `/forgot-password` - Request password reset
- GET/POST `/reset-password` - Reset password with token
- GET `/profile` - User profile page
- GET `/admin/users` - Admin user management

### Step 4: Update login to work with new user system

Modify the existing `/auth/login` endpoint to:
1. Check user exists with `get_user_by_email()`
2. Verify user is active and verified
3. Call `update_last_login()` on success

### Step 5: Update security middleware

The `AuthEnforcementMiddleware` already checks for cookies, so it will work.
Just ensure `/register`, `/verify-email`, `/forgot-password`, `/reset-password` are in PUBLIC_ROUTES.

## 🚀 Quick Deploy Script

I'll create an automated deployment script that:
1. Backs up current app.py
2. Integrates all changes
3. Tests syntax
4. Commits to git
5. Pushes to development branch

## 📝 Default Admin Credentials

After deployment, the system automatically creates:

```
Email:    admin@entity-validator.local
Password: ChangeMe123!
```

⚠️ **IMPORTANT**: Change this password immediately after first login!

## ✅ Testing Checklist

After deployment:

1. **Registration Flow**
   - Go to `/register`
   - Create new user account
   - Check console for verification link (email not configured yet)
   - Click verification link
   - Verify you can login

2. **Login Flow**
   - Go to `/login`
   - Login with admin credentials
   - Verify you're redirected to `/batch-validate`

3. **Password Reset**
   - Go to `/forgot-password`
   - Request reset for your email
   - Check console for reset link
   - Click reset link and set new password
   - Login with new password

4. **Batch Processing**
   - Upload a batch file
   - Verify jobs process correctly (not stuck in pending)

## 🔐 Security Features

### Authentication
- ✅ Bcrypt password hashing
- ✅ Email verification required
- ✅ Account lockout after 5 failed attempts (15 min)
- ✅ Rate limiting on registration (5/hour)
- ✅ Rate limiting on password reset (3/hour)

### Session Management
- ✅ JWT with 30-minute expiration
- ✅ HttpOnly cookies
- ✅ Secure flag in production
- ✅ Token blacklist on logout

### CSRF Protection
- ✅ All POST/PUT/DELETE require CSRF token
- ✅ Auto-generated on page load
- ✅ Validated server-side

### Audit Logging
- ✅ User registration
- ✅ Login attempts (success/fail)
- ✅ Password resets
- ✅ Account lockouts
- ✅ All admin actions

## 📧 Email Configuration (Optional)

To enable actual email sending, configure SMTP in environment variables:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=noreply@entity-validator.local
```

Then update `send_verification_email()` and `send_password_reset_email()` 
in `user_management.py` to use actual SMTP instead of console logging.

## 🎯 Next Steps

1. Run the deployment script
2. Test all flows
3. Change default admin password
4. Configure email (optional)
5. Create additional admin users if needed
6. Schedule CREST pen test

## 🔄 Rollback Plan

If anything goes wrong:
1. Railway UI → Previous deployment → Redeploy
2. Or: `git revert HEAD && git push origin development`

Backups available at: `/backups/crest-hardening-2026-01-21/`

---

**Status**: Ready to deploy
**Estimated time**: 5 minutes
**Risk**: Low (full backups available)

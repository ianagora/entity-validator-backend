#!/usr/bin/env python3
"""
Automated User Management Integration Script
Integrates user management system into app.py
"""

import re
import sys

# Read app.py
with open('app.py', 'r') as f:
    app_content = f.read()

# 1. Add user_management import after security imports
user_mgmt_import = """
# User Management System
from user_management import (
    init_user_management, create_user, verify_email, get_user_by_email,
    update_last_login, create_password_reset_token,
    reset_password_with_token, send_verification_email, send_password_reset_email
)
"""

# Find security import block and add after it
if 'from user_management import' not in app_content:
    app_content = re.sub(
        r'(from security_middleware import[\s\S]*?\))',
        r'\1\n' + user_mgmt_import,
        app_content,
        count=1
    )
    print("✓ Added user_management imports")
else:
    print("✓ user_management imports already present")

# 2. Add init_user_management() to lifespan
if 'init_user_management()' not in app_content:
    app_content = re.sub(
        r'(init_db\(\))',
        r'\1\n    init_user_management()  # Initialize user management system',
        app_content,
        count=1
    )
    print("✓ Added init_user_management() to startup")
else:
    print("✓ init_user_management() already in startup")

# 3. Update PUBLIC_ROUTES in middleware to include new routes
public_routes_addition = """        "/register",
        "/verify-email",
        "/forgot-password",
        "/reset-password","""

if '"/register"' not in app_content:
    app_content = re.sub(
        r'(PUBLIC_ROUTES = \{[\s]*"/health",)',
        r'\1\n' + public_routes_addition,
        app_content,
        count=1
    )
    print("✓ Added new routes to PUBLIC_ROUTES")
else:
    print("✓ New routes already in PUBLIC_ROUTES")

# 4. Add user routes before existing auth endpoints
user_routes = open('/tmp/user_routes.py').read()

# Find where to insert (before @app.get("/login"))
if '@app.get("/register")' not in app_content:
    app_content = re.sub(
        r'(# ============================================================================\n# AUTHENTICATION & AUTHORIZATION ENDPOINTS)',
        r'\1\n\n' + user_routes + '\n',
        app_content,
        count=1
    )
    print("✓ Added user management routes")
else:
    print("✓ User management routes already present")

# Write updated app.py
with open('app.py', 'w') as f:
    f.write(app_content)

print("\n✅ Integration complete!")
print("\nNext steps:")
print("1. Test syntax: python3 -m py_compile app.py")
print("2. Commit: git add . && git commit -m 'feat: Add complete user management system'")
print("3. Push: git push origin development")


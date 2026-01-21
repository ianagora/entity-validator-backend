#!/usr/bin/env python3
"""Test startup sequence to debug Railway 502 errors."""
import sys
import os

print("=" * 60)
print("🔍 STARTUP DIAGNOSTICS")
print("=" * 60)

# Test 1: Check Python version
print(f"\n1. Python version: {sys.version}")

# Test 2: Check environment
print(f"\n2. Environment variables:")
print(f"   - PORT: {os.getenv('PORT', 'NOT SET')}")
print(f"   - DB_PATH: {os.getenv('DB_PATH', 'NOT SET')}")
print(f"   - ENVIRONMENT: {os.getenv('ENVIRONMENT', 'NOT SET')}")

# Test 3: Try importing critical modules
print(f"\n3. Testing imports...")
try:
    print("   - Importing fastapi...", end=" ")
    import fastapi
    print(f"✓ (v{fastapi.__version__})")
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

try:
    print("   - Importing security...", end=" ")
    import security
    print("✓")
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

try:
    print("   - Importing user_management...", end=" ")
    import user_management
    print("✓")
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

try:
    print("   - Importing security_middleware...", end=" ")
    import security_middleware
    print("✓")
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

# Test 4: Try initializing user management
print(f"\n4. Testing user_management.init_user_management()...")
try:
    user_management.init_user_management()
    print("   ✓ User management initialized successfully")
except Exception as e:
    print(f"   ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 5: Try importing app
print(f"\n5. Testing app import...")
try:
    import app
    print("   ✓ App imported successfully")
except Exception as e:
    print(f"   ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ ALL TESTS PASSED - App should start normally")
print("=" * 60)

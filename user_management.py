# user_management.py - Complete User Management System
"""
User management system with:
- User registration & email verification
- Password reset flow
- Role-based access control (admin, user, viewer)
- Session management
- Default admin account creation
"""

import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any, List
import sqlite3
from contextlib import contextmanager

from security import get_password_hash, verify_password


# ==============================================================================
# DATABASE SETUP
# ==============================================================================

DB_PATH = os.getenv("DB_PATH", "/data/entity_workflow.db") if os.path.exists("/data") else "entity_workflow.db"


@contextmanager
def get_db():
    """Database context manager."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_user_tables():
    """Initialize user management tables."""
    with get_db() as conn:
        # Users table with enhanced fields
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                is_active INTEGER NOT NULL DEFAULT 0,
                is_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login TEXT,
                login_count INTEGER DEFAULT 0,
                failed_login_attempts INTEGER DEFAULT 0,
                locked_until TEXT
            )
        """)
        
        # Email verification tokens
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # Password reset tokens
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # User sessions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_token TEXT UNIQUE NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_verification_token ON email_verification_tokens(token)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reset_token ON password_reset_tokens(token)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session_token ON user_sessions(session_token)")
        
        print("[USER_MGMT] ✓ User management tables initialized")


def create_default_admin():
    """Create default admin user if none exists."""
    with get_db() as conn:
        # Check if any admin exists
        admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if admin:
            print("[USER_MGMT] ✓ Admin user already exists")
            return
        
        # Create default admin
        email = "admin@entity-validator.local"
        password = "ChangeMe123!"  # MUST be changed on first login
        name = "System Administrator"
        
        password_hash = get_password_hash(password)
        now = datetime.utcnow().isoformat() + "Z"
        
        conn.execute("""
            INSERT INTO users (email, name, password_hash, role, is_active, is_verified, created_at, updated_at)
            VALUES (?, ?, ?, 'admin', 1, 1, ?, ?)
        """, (email, name, password_hash, now, now))
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║          ✓ DEFAULT ADMIN USER CREATED                       ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

Email:    {email}
Password: {password}

⚠️  IMPORTANT: Change this password immediately after first login!

Login at: /login
        """)


# ==============================================================================
# USER CRUD OPERATIONS
# ==============================================================================

def create_user(email: str, name: str, password: str, role: str = "user") -> Dict[str, Any]:
    """Create a new user."""
    with get_db() as conn:
        # Check if user exists
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
        if existing:
            raise ValueError("User with this email already exists")
        
        password_hash = get_password_hash(password)
        now = datetime.utcnow().isoformat() + "Z"
        
        cursor = conn.execute("""
            INSERT INTO users (email, name, password_hash, role, is_active, is_verified, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, 0, ?, ?)
        """, (email.lower(), name, password_hash, role, now, now))
        
        user_id = cursor.lastrowid
        
        # Generate verification token
        verification_token = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(hours=24)).isoformat() + "Z"
        
        conn.execute("""
            INSERT INTO email_verification_tokens (user_id, token, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, verification_token, now, expires_at))
        
        return {
            "id": user_id,
            "email": email.lower(),
            "name": name,
            "role": role,
            "verification_token": verification_token
        }


def verify_email(token: str) -> bool:
    """Verify user email with token."""
    with get_db() as conn:
        # Get token
        token_row = conn.execute("""
            SELECT user_id, expires_at, used 
            FROM email_verification_tokens 
            WHERE token=?
        """, (token,)).fetchone()
        
        if not token_row:
            return False
        
        if token_row["used"]:
            return False
        
        # Check expiration
        expires_at = datetime.fromisoformat(token_row["expires_at"].replace("Z", ""))
        if datetime.utcnow() > expires_at:
            return False
        
        # Activate user
        conn.execute("""
            UPDATE users 
            SET is_active=1, is_verified=1, updated_at=?
            WHERE id=?
        """, (datetime.utcnow().isoformat() + "Z", token_row["user_id"]))
        
        # Mark token as used
        conn.execute("""
            UPDATE email_verification_tokens 
            SET used=1 
            WHERE token=?
        """, (token,))
        
        return True


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Get user by email."""
    with get_db() as conn:
        user = conn.execute("""
            SELECT id, email, name, password_hash, role, is_active, is_verified,
                   created_at, updated_at, last_login, login_count
            FROM users 
            WHERE email=?
        """, (email.lower(),)).fetchone()
        
        if user:
            return dict(user)
        return None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user by ID."""
    with get_db() as conn:
        user = conn.execute("""
            SELECT id, email, name, role, is_active, is_verified,
                   created_at, updated_at, last_login, login_count
            FROM users 
            WHERE id=?
        """, (user_id,)).fetchone()
        
        if user:
            return dict(user)
        return None


def update_last_login(user_id: int):
    """Update user's last login timestamp."""
    with get_db() as conn:
        now = datetime.utcnow().isoformat() + "Z"
        conn.execute("""
            UPDATE users 
            SET last_login=?, login_count=login_count+1, updated_at=?
            WHERE id=?
        """, (now, now, user_id))


def list_users(role: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """List users with optional role filter."""
    with get_db() as conn:
        if role:
            users = conn.execute("""
                SELECT id, email, name, role, is_active, is_verified, created_at, last_login, login_count
                FROM users 
                WHERE role=?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (role, limit, offset)).fetchall()
        else:
            users = conn.execute("""
                SELECT id, email, name, role, is_active, is_verified, created_at, last_login, login_count
                FROM users 
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()
        
        return [dict(u) for u in users]


# ==============================================================================
# PASSWORD RESET
# ==============================================================================

def create_password_reset_token(email: str) -> Optional[str]:
    """Create password reset token."""
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
        if not user:
            return None
        
        token = secrets.token_urlsafe(32)
        now = datetime.utcnow().isoformat() + "Z"
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"
        
        conn.execute("""
            INSERT INTO password_reset_tokens (user_id, token, created_at, expires_at)
            VALUES (?, ?, ?, ?)
        """, (user["id"], token, now, expires_at))
        
        return token


def reset_password_with_token(token: str, new_password: str) -> bool:
    """Reset password using token."""
    with get_db() as conn:
        # Get token
        token_row = conn.execute("""
            SELECT user_id, expires_at, used 
            FROM password_reset_tokens 
            WHERE token=?
        """, (token,)).fetchone()
        
        if not token_row:
            return False
        
        if token_row["used"]:
            return False
        
        # Check expiration
        expires_at = datetime.fromisoformat(token_row["expires_at"].replace("Z", ""))
        if datetime.utcnow() > expires_at:
            return False
        
        # Update password
        password_hash = get_password_hash(new_password)
        now = datetime.utcnow().isoformat() + "Z"
        
        conn.execute("""
            UPDATE users 
            SET password_hash=?, updated_at=?
            WHERE id=?
        """, (password_hash, now, token_row["user_id"]))
        
        # Mark token as used
        conn.execute("""
            UPDATE password_reset_tokens 
            SET used=1 
            WHERE token=?
        """, (token,))
        
        return True


# ==============================================================================
# EMAIL SENDING (MOCK FOR NOW, IMPLEMENT SMTP LATER)
# ==============================================================================

def send_verification_email(email: str, token: str, base_url: str):
    """Send email verification link."""
    verification_url = f"{base_url}/verify-email?token={token}"
    
    # TODO: Implement actual email sending with SMTP
    # For now, just log it
    print(f"""
[EMAIL] Verification email for {email}
Click to verify: {verification_url}
    """)


def send_password_reset_email(email: str, token: str, base_url: str):
    """Send password reset link."""
    reset_url = f"{base_url}/reset-password?token={token}"
    
    # TODO: Implement actual email sending with SMTP
    print(f"""
[EMAIL] Password reset for {email}
Click to reset: {reset_url}
    """)


# ==============================================================================
# INITIALIZATION
# ==============================================================================

def init_user_management():
    """Initialize user management system."""
    init_user_tables()
    create_default_admin()


if __name__ == "__main__":
    init_user_management()

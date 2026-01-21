# api_key_management.py - Permanent API Key System for Frontend/Backend Auth

"""
API Key Management System
- Generate long-lived API keys for frontend authentication
- Support key rotation without downtime
- Audit logging for key usage
- Key expiration and revocation
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

# Database path (same as app.py)
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


def init_api_key_tables():
    """Initialize API key management tables."""
    with get_db() as conn:
        # API Keys table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id TEXT UNIQUE NOT NULL,
                key_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                scopes TEXT NOT NULL DEFAULT 'api:read,api:write',
                created_by INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                usage_count INTEGER DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        
        # API Key usage logs (for auditing)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_key_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                status_code INTEGER,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (key_id) REFERENCES api_keys(key_id) ON DELETE CASCADE
            )
        """)
        
        # Create index for faster lookups
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_keys_key_id 
            ON api_keys(key_id) WHERE is_active=1
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_key_usage_timestamp 
            ON api_key_usage_logs(timestamp)
        """)
        
        print("[API_KEY_MGMT] ✓ Tables initialized")


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.
    
    Returns:
        tuple: (key_id, full_key, key_hash)
        - key_id: Unique identifier (stored in DB)
        - full_key: Complete API key to give to user (store securely, never shown again)
        - key_hash: Hashed version for DB storage
    """
    # Generate key_id (short identifier)
    key_id = f"ak_{secrets.token_urlsafe(16)}"
    
    # Generate the secret part (this is what user will use)
    secret = secrets.token_urlsafe(32)
    
    # Full API key format: ak_xxxxx.secret_yyyyy
    full_key = f"{key_id}.{secret}"
    
    # Hash for storage (we'll verify by reconstructing the full key)
    import hashlib
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    
    return key_id, full_key, key_hash


def create_api_key(
    name: str,
    description: str = "",
    scopes: str = "api:read,api:write",
    created_by: Optional[int] = None,
    expires_in_days: Optional[int] = None
) -> Dict[str, Any]:
    """
    Create a new API key.
    
    Args:
        name: Human-readable name for the key
        description: Optional description
        scopes: Comma-separated list of scopes (default: api:read,api:write)
        created_by: User ID who created this key
        expires_in_days: Days until expiration (None = never expires)
    
    Returns:
        dict: Contains 'key_id', 'api_key' (full key - SAVE THIS!), and metadata
    """
    key_id, full_key, key_hash = generate_api_key()
    
    now = datetime.utcnow().isoformat() + "Z"
    expires_at = None
    if expires_in_days:
        expires_at = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat() + "Z"
    
    with get_db() as conn:
        conn.execute("""
            INSERT INTO api_keys (key_id, key_hash, name, description, scopes, created_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (key_id, key_hash, name, description, scopes, created_by, now, expires_at))
    
    print(f"[API_KEY_MGMT] ✓ Created API key: {name} (ID: {key_id})")
    
    return {
        "key_id": key_id,
        "api_key": full_key,  # ⚠️ ONLY TIME THIS IS SHOWN - SAVE IT!
        "name": name,
        "description": description,
        "scopes": scopes.split(","),
        "created_at": now,
        "expires_at": expires_at
    }


def verify_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Verify an API key and return its details.
    
    Args:
        api_key: Full API key (ak_xxxxx.secret_yyyyy)
    
    Returns:
        dict: Key details if valid, None if invalid
    """
    import hashlib
    
    # Extract key_id from the full key
    if "." not in api_key:
        return None
    
    key_id = api_key.split(".")[0]
    
    # Hash the full key for comparison
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    
    with get_db() as conn:
        key_record = conn.execute("""
            SELECT * FROM api_keys 
            WHERE key_id=? AND key_hash=? AND is_active=1
        """, (key_id, key_hash)).fetchone()
        
        if not key_record:
            return None
        
        # Check expiration
        if key_record['expires_at']:
            expires_at = datetime.fromisoformat(key_record['expires_at'].replace('Z', '+00:00'))
            if datetime.utcnow().replace(tzinfo=expires_at.tzinfo) > expires_at:
                print(f"[API_KEY_MGMT] ⚠️  API key expired: {key_id}")
                return None
        
        # Update last_used_at and usage_count
        now = datetime.utcnow().isoformat() + "Z"
        conn.execute("""
            UPDATE api_keys 
            SET last_used_at=?, usage_count=usage_count+1 
            WHERE key_id=?
        """, (now, key_id))
        
        return {
            "key_id": key_record['key_id'],
            "name": key_record['name'],
            "scopes": key_record['scopes'].split(","),
            "created_at": key_record['created_at'],
            "expires_at": key_record['expires_at'],
            "last_used_at": now,
            "usage_count": key_record['usage_count'] + 1
        }


def log_api_key_usage(
    key_id: str,
    endpoint: str,
    method: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    status_code: int = 200
):
    """Log API key usage for auditing."""
    now = datetime.utcnow().isoformat() + "Z"
    
    with get_db() as conn:
        conn.execute("""
            INSERT INTO api_key_usage_logs (key_id, endpoint, method, ip_address, user_agent, status_code, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (key_id, endpoint, method, ip_address, user_agent, status_code, now))


def list_api_keys(include_inactive: bool = False) -> List[Dict[str, Any]]:
    """List all API keys (without showing the actual key)."""
    with get_db() as conn:
        query = "SELECT * FROM api_keys"
        if not include_inactive:
            query += " WHERE is_active=1"
        query += " ORDER BY created_at DESC"
        
        keys = conn.execute(query).fetchall()
        
        return [
            {
                "key_id": k['key_id'],
                "name": k['name'],
                "description": k['description'],
                "scopes": k['scopes'].split(","),
                "created_at": k['created_at'],
                "expires_at": k['expires_at'],
                "last_used_at": k['last_used_at'],
                "usage_count": k['usage_count'],
                "is_active": bool(k['is_active'])
            }
            for k in keys
        ]


def revoke_api_key(key_id: str) -> bool:
    """Revoke (deactivate) an API key."""
    with get_db() as conn:
        result = conn.execute("""
            UPDATE api_keys SET is_active=0 WHERE key_id=?
        """, (key_id,))
        
        if result.rowcount > 0:
            print(f"[API_KEY_MGMT] ✓ Revoked API key: {key_id}")
            return True
        return False


def create_default_frontend_api_key():
    """Create a default API key for frontend if none exists."""
    with get_db() as conn:
        # Check if a frontend key exists
        existing = conn.execute("""
            SELECT key_id FROM api_keys 
            WHERE name='Frontend Default' AND is_active=1
            LIMIT 1
        """).fetchone()
        
        if existing:
            print("[API_KEY_MGMT] ✓ Frontend API key already exists")
            return None
        
        # Create default key (expires in 1 year)
        key_info = create_api_key(
            name="Frontend Default",
            description="Default API key for development frontend (Cloudflare Pages)",
            scopes="api:read,api:write,batch:upload",
            expires_in_days=365
        )
        
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║          ✓ FRONTEND API KEY CREATED                         ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

⚠️  SAVE THIS API KEY - IT WILL NOT BE SHOWN AGAIN!

API Key: {key_info['api_key']}

Configure in Cloudflare Pages:
  BACKEND_API_KEY={key_info['api_key']}

Key ID:      {key_info['key_id']}
Name:        {key_info['name']}
Scopes:      {', '.join(key_info['scopes'])}
Expires:     {key_info['expires_at'] or 'Never'}

╔══════════════════════════════════════════════════════════════╗
        """)
        
        return key_info


def init_api_key_management():
    """Initialize API key system."""
    init_api_key_tables()
    create_default_frontend_api_key()


if __name__ == "__main__":
    # Test the system
    init_api_key_management()
    
    print("\n--- Testing API Key System ---")
    
    # List keys
    keys = list_api_keys()
    print(f"\nActive API keys: {len(keys)}")
    for key in keys:
        print(f"  - {key['name']} (ID: {key['key_id']}, Usage: {key['usage_count']})")

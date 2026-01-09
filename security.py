# security.py - Comprehensive Security Module for CREST Compliance

import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import re
import sqlite3

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, constr, validator
from slowapi import Limiter
from slowapi.util import get_remote_address

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))  # Generate if not set
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# File Upload Configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {'.xlsx', '.csv', '.xls'}

# Rate Limiting Configuration
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"

# ==============================================================================
# PASSWORD HASHING (Phase 1: Authentication)
# ==============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Generate bcrypt hash for a password."""
    return pwd_context.hash(password)

# ==============================================================================
# JWT TOKEN MANAGEMENT (Phase 3: Session Management)
# ==============================================================================

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

# ==============================================================================
# USER AUTHENTICATION (Phase 1: Authentication & Authorization)
# ==============================================================================

def get_db_connection():
    """Get database connection - import from main app."""
    import sqlite3
    DB_PATH = os.getenv("DB_PATH", "entity_workflow.db")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[Dict]:
    """
    Get the current authenticated user from JWT token.
    Returns None if no token provided (for optional auth).
    Raises HTTPException if token is invalid.
    """
    if not token:
        return None
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = decode_token(token)
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        
        if user_id is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    # Get user from database
    conn = get_db_connection()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE id=? AND is_active=1",
            (user_id,)
        ).fetchone()
        if user is None:
            raise credentials_exception
        
        # Convert Row to dict
        user_dict = dict(user)
        return user_dict
    finally:
        conn.close()

async def get_current_active_user(current_user: Dict = Depends(get_current_user)) -> Dict:
    """Get current user, raising error if not authenticated."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user

async def get_current_admin_user(current_user: Dict = Depends(get_current_active_user)) -> Dict:
    """Get current user and verify admin role."""
    conn = get_db_connection()
    try:
        roles = conn.execute("""
            SELECT r.name FROM roles r
            JOIN user_roles ur ON ur.role_id = r.id
            WHERE ur.user_id = ?
        """, (current_user["id"],)).fetchall()
        
        role_names = [r["name"] for r in roles]
        if "admin" not in role_names:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        return current_user
    finally:
        conn.close()

# ==============================================================================
# INPUT VALIDATION (Phase 2: Input Validation)
# ==============================================================================

class UserCreate(BaseModel):
    """Validated user creation model."""
    email: EmailStr
    full_name: constr(min_length=2, max_length=100)
    password: constr(min_length=12, max_length=128)
    
    @validator('password')
    def password_strength(cls, v):
        """Enforce strong password policy."""
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one digit')
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
            raise ValueError('Password must contain at least one special character')
        return v
    
    @validator('full_name')
    def name_valid(cls, v):
        """Prevent XSS in names."""
        if re.search(r'[<>]', v):
            raise ValueError('Invalid characters in name')
        return v.strip()

class UserLogin(BaseModel):
    """Validated login model."""
    email: EmailStr
    password: str

def validate_file_upload(filename: str, content: bytes) -> None:
    """
    Validate file upload for security.
    Raises HTTPException if validation fails.
    """
    # Check file size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB."
        )
    
    # Check file extension
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Sanitize filename
    if not re.match(r'^[\w\-. ]+$', filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename. Only alphanumeric, dash, underscore, dot, and space allowed."
        )
    
    # Check for null bytes (path traversal)
    if '\x00' in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename"
        )

def sanitize_sql_input(value: str) -> str:
    """Sanitize input for SQL queries (in addition to parameterized queries)."""
    if value is None:
        return None
    # Remove any SQL metacharacters
    dangerous_chars = ["'", '"', ';', '--', '/*', '*/']
    for char in dangerous_chars:
        if char in value:
            raise ValueError(f"Invalid input: contains forbidden character {char}")
    return value.strip()

# ==============================================================================
# RATE LIMITING (Phase 5: API Security)
# ==============================================================================

limiter = Limiter(key_func=get_remote_address, enabled=RATE_LIMIT_ENABLED)

# ==============================================================================
# AUDIT LOGGING (Phase 7: Logging & Monitoring)
# ==============================================================================

def init_audit_log_table():
    """Initialize audit log table if it doesn't exist."""
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_id INTEGER,
                user_email TEXT,
                action TEXT NOT NULL,
                resource_type TEXT,
                resource_id TEXT,
                ip_address TEXT,
                user_agent TEXT,
                status TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)
        """)
        conn.commit()
    finally:
        conn.close()

def log_audit_event(
    action: str,
    status: str = "success",
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[str] = None
) -> None:
    """Log an audit event to the database."""
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO audit_logs (
                timestamp, user_id, user_email, action, 
                resource_type, resource_id, ip_address, user_agent, status, details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat() + 'Z',
            user_id,
            user_email,
            action,
            resource_type,
            resource_id,
            ip_address,
            user_agent,
            status,
            details
        ))
        conn.commit()
    except Exception as e:
        print(f"[AUDIT LOG ERROR] Failed to log event: {e}")
    finally:
        conn.close()

def log_request(request: Request, current_user: Optional[Dict], action: str, status: str = "success", details: Optional[str] = None):
    """Helper to log a request with user context."""
    log_audit_event(
        action=action,
        status=status,
        user_id=current_user["id"] if current_user else None,
        user_email=current_user["email"] if current_user else None,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details=details
    )

# ==============================================================================
# SECURITY MIDDLEWARE HELPERS (Phase 6: Infrastructure)
# ==============================================================================

def get_security_headers() -> Dict[str, str]:
    """Get recommended security headers for responses."""
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        ),
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()"
    }

# ==============================================================================
# ENCRYPTION UTILITIES (Phase 4: Data Protection)
# ==============================================================================

def hash_sensitive_data(data: str) -> str:
    """Hash sensitive data for storage (one-way)."""
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

# Note: For reversible encryption, use Fernet (requires separate ENCRYPTION_KEY)
# from cryptography.fernet import Fernet
# ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
# fernet = Fernet(ENCRYPTION_KEY.encode())

# ==============================================================================
# INITIALIZATION
# ==============================================================================

def init_security():
    """Initialize security components."""
    init_audit_log_table()
    print("[SECURITY] Security module initialized")
    print(f"[SECURITY] Rate limiting: {'enabled' if RATE_LIMIT_ENABLED else 'disabled'}")
    print(f"[SECURITY] JWT secret: {'configured' if os.getenv('JWT_SECRET_KEY') else 'using default (change in production!)'}")

# Initialize on import
init_security()

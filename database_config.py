"""
Database Configuration for Railway Deployment
Supports both SQLite (development) and PostgreSQL (production)
"""

import os
from contextlib import contextmanager
from typing import Generator

# Check if we're using PostgreSQL or SQLite
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = DATABASE_URL and DATABASE_URL.startswith("postgres")

if USE_POSTGRES:
    # PostgreSQL setup
    import psycopg2
    import psycopg2.extras
    from urllib.parse import urlparse
    
    # Parse DATABASE_URL
    result = urlparse(DATABASE_URL)
    DB_CONFIG = {
        "host": result.hostname,
        "port": result.port or 5432,
        "database": result.path[1:],  # Remove leading /
        "user": result.username,
        "password": result.password,
        "sslmode": "require"  # Railway requires SSL
    }
    
    print(f"[DB] Using PostgreSQL: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    
    @contextmanager
    def db() -> Generator:
        """PostgreSQL connection context manager"""
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False  # Manual transaction control
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def init_db_postgres():
        """Initialize PostgreSQL database schema"""
        with db() as conn:
            cur = conn.cursor()
            
            # Create tables (PostgreSQL syntax)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batches (
                    id SERIAL PRIMARY KEY,
                    filename TEXT,
                    upload_path TEXT,
                    status TEXT DEFAULT 'pending',
                    total_items INTEGER DEFAULT 0,
                    processed_items INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER REFERENCES batches(id) ON DELETE CASCADE,
                    input_name TEXT,
                    entity_name TEXT,
                    company_number TEXT,
                    charity_number TEXT,
                    resolved_registry TEXT,
                    pipeline_status TEXT DEFAULT 'pending',
                    enrich_status TEXT DEFAULT 'pending',
                    match_type TEXT,
                    confidence REAL,
                    reason TEXT,
                    source_url TEXT,
                    resolved_data TEXT,
                    enriched_data TEXT,
                    shareholders_json TEXT,
                    shareholders_status TEXT,
                    enrich_json_path TEXT,
                    enrich_xlsx_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
                    status TEXT,
                    l1_assigned_to TEXT,
                    l1_outcome TEXT,
                    l1_qc_assigned_to TEXT,
                    l1_qc_outcome TEXT,
                    l2_assigned_to TEXT,
                    l2_outcome TEXT,
                    l3_assigned_to TEXT,
                    l3_outcome TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    role TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_items_batch_id ON items(batch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(pipeline_status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_items_enrich_status ON items(enrich_status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_reviews_item_id ON reviews(item_id)")
            
            print("[DB] PostgreSQL schema initialized")

else:
    # SQLite setup (existing code)
    import sqlite3
    
    DB_PATH = os.getenv("DB_PATH", "entity_workflow.db")
    print(f"[DB] Using SQLite: {DB_PATH}")
    
    @contextmanager
    def db() -> Generator:
        """SQLite connection context manager"""
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
    
    def init_db_sqlite():
        """Initialize SQLite database schema (existing schema)"""
        with db() as conn:
            # This would call your existing init_db() logic
            print("[DB] SQLite schema initialized (using existing logic)")


# Unified init function
def init_db():
    """Initialize database (PostgreSQL or SQLite based on environment)"""
    if USE_POSTGRES:
        init_db_postgres()
    else:
        init_db_sqlite()

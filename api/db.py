"""
Database helpers for the UMarket Bot.
Uses Neon PostgreSQL (free tier) via DATABASE_URL environment variable.
Tables are created automatically by calling init_db().
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_connection():
    """Return a new psycopg2 connection using DATABASE_URL."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set.")
    conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
    return conn


def init_db():
    """
    Create all required tables if they don't already exist.
    Call this once after deployment via GET /setup.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # --- Users ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   BIGINT PRIMARY KEY,
                username  TEXT,
                joined_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # --- User details (email) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_details (
                user_id BIGINT PRIMARY KEY,
                email   TEXT
            )
        """)

        # --- Checker codes (replaces .txt files) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS checker_codes (
                id            SERIAL PRIMARY KEY,
                exam_type     TEXT    NOT NULL,
                pin           TEXT    NOT NULL,
                serial_number TEXT    NOT NULL,
                is_used       BOOLEAN DEFAULT FALSE,
                added_at      TIMESTAMP DEFAULT NOW()
            )
        """)

        # --- Transactions (Paystack) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id              SERIAL PRIMARY KEY,
                user_id         BIGINT,
                exam_type       TEXT,
                quantity        INTEGER DEFAULT 1,
                transaction_ref TEXT UNIQUE,
                status          TEXT DEFAULT 'Pending',
                amount          REAL,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)

        # --- Sales log ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id        SERIAL PRIMARY KEY,
                user_id   BIGINT,
                exam_type TEXT,
                quantity  INTEGER,
                amount    REAL,
                sold_at   TIMESTAMP DEFAULT NOW()
            )
        """)

        # --- Referrals ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id          SERIAL PRIMARY KEY,
                referrer_id BIGINT,
                referred_id BIGINT UNIQUE,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)

        # --- Points ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS points (
                user_id BIGINT PRIMARY KEY,
                points  INTEGER DEFAULT 0
            )
        """)

        # --- User sessions (state machine — replaces in-memory dicts) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id    BIGINT PRIMARY KEY,
                email      TEXT,
                exam_type  TEXT,
                state      TEXT,
                state_data TEXT
            )
        """)

        # --- Support tickets ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                issue      TEXT,
                status     TEXT DEFAULT 'Open',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        conn.commit()
        print("✅ Database initialised successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"❌ Database init error: {exc}")
        raise
    finally:
        cur.close()
        conn.close()

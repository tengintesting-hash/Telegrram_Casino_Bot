import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def fetch_one(query, params=None):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or {})
            return cur.fetchone()


def fetch_all(query, params=None):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or {})
            return cur.fetchall()


def execute(query, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or {})
        conn.commit()

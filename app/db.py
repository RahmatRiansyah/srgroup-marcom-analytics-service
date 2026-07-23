import os
from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error as MySQLError


def get_db_config() -> dict:
    """Baca konfigurasi database dari environment variable (.env).

    Disamakan dengan koneksi yang dipakai scraper.py & Laravel, supaya
    kedua service selalu bicara ke database yang sama.
    """
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_DATABASE", "db_marcom_analytics"),
    }


@contextmanager
def get_connection():
    """Context manager supaya koneksi selalu ditutup meskipun terjadi error."""
    conn = None
    try:
        conn = mysql.connector.connect(**get_db_config())
        yield conn
    except MySQLError as e:
        raise RuntimeError(f"Gagal konek ke database: {e}") from e
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows


def fetch_one(query: str, params: tuple = ()) -> dict | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None

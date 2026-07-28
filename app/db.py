import os
from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error as MySQLError
from mysql.connector.pooling import MySQLConnectionPool

_pool: MySQLConnectionPool | None = None


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


def _get_pool() -> MySQLConnectionPool:
    """Buat connection pool sekali saja (lazy singleton), dipakai ulang di
    semua request. Sebelumnya tiap fetch_all/fetch_one buka koneksi TCP baru
    ke MySQL dan menutupnya lagi -- boros & jadi bottleneck pertama begitu
    traffic naik. Ukuran pool diatur lewat DB_POOL_SIZE (default 5), samakan
    dengan jumlah worker Uvicorn supaya tidak saling rebutan/timeout.
    """
    global _pool
    if _pool is None:
        pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        _pool = MySQLConnectionPool(
            pool_name="srgroup_analytics_pool",
            pool_size=pool_size,
            pool_reset_session=True,
            **get_db_config(),
        )
    return _pool


@contextmanager
def get_connection():
    """Context manager: pinjam koneksi dari pool, otomatis dikembalikan
    (bukan ditutup permanen) begitu selesai dipakai."""
    conn = None
    try:
        conn = _get_pool().get_connection()
        yield conn
    except MySQLError as e:
        raise RuntimeError(f"Gagal konek ke database: {e}") from e
    finally:
        if conn is not None and conn.is_connected():
            conn.close()  # mengembalikan koneksi ke pool, bukan menutup TCP-nya


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

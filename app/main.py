import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query

from app.auth import verify_api_key
from app.db import fetch_all, fetch_one
from app.google_trends import get_live_trend
from app.meta_insights import (
    MetaApiError,
    MetaConfigError,
    get_engagement_summary,
    get_recent_meta_posts,
    sync_meta_account,
)
from scraper import run_scraper

load_dotenv()

# Default AMAN: dokumentasi (/docs, /redoc, /openapi.json) hanya aktif kalau
# APP_ENV secara eksplisit diset ke "local"/"development". Kalau env var ini
# belum diset sama sekali (mis. lupa dikonfigurasi saat deploy), service
# tetap fallback ke mode "tersembunyi" -- bukan malah kebuka ke publik.
_is_local_env = os.getenv("APP_ENV", "production").lower() in ("local", "development", "dev")

app = FastAPI(
    title="SRGroup Marcom Analytics Service",
    description=(
        "Mesin analisis tren & kompetitor untuk chatbot marketing SR Group. "
        "Semua endpoint di bawah /trends, /competitor, /summary butuh header "
        "X-API-Key."
    ),
    version="0.1.0",
    docs_url="/docs" if _is_local_env else None,
    redoc_url="/redoc" if _is_local_env else None,
    openapi_url="/openapi.json" if _is_local_env else None,
)


# ---------------------------------------------------------------------------
# Health check (tidak butuh API key, dipakai untuk cek service hidup/deploy)
# ---------------------------------------------------------------------------
@app.get("/")
def health_check():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /trends?keyword=...
# Cari tren/postingan yang relevan dengan sebuah keyword, lintas semua
# sumber (kompetitor / platform) yang aktif dipantau.
# ---------------------------------------------------------------------------
@app.get("/trends", dependencies=[Depends(verify_api_key)])
def get_trends(
    keyword: Optional[str] = Query(
        default=None, description="Kata kunci pencarian, contoh: 'diskon lebaran'"
    ),
    limit: int = Query(default=20, ge=1, le=100),
    days: int = Query(
        default=30,
        ge=1,
        le=365,
        description=(
            "Batasi hasil hanya postingan yang di-scrape dalam N hari terakhir. "
            "Ini SENGAJA jadi default (bukan opsional) supaya endpoint ini tidak pernah "
            "mengembalikan data lama (mis. hasil scraping 3 bulan lalu) yang bisa disalahartikan "
            "sebagai tren yang sedang terjadi sekarang. Perbesar nilainya kalau memang butuh histori lebih jauh."
        ),
    ),
):
    since = datetime.now() - timedelta(days=days)

    if keyword:
        like = f"%{keyword}%"
        rows = fetch_all(
            """
            SELECT
                tp.id, tp.title, tp.content, tp.post_url, tp.posted_at, tp.created_at,
                ts.name AS source_name, ts.platform AS source_platform
            FROM trend_posts tp
            JOIN trend_sources ts ON ts.id = tp.trend_source_id
            WHERE (tp.title LIKE %s OR tp.content LIKE %s OR ts.name LIKE %s)
              AND tp.created_at >= %s
            ORDER BY tp.created_at DESC
            LIMIT %s
            """,
            (like, like, like, since, limit),
        )
    else:
        rows = fetch_all(
            """
            SELECT
                tp.id, tp.title, tp.content, tp.post_url, tp.posted_at, tp.created_at,
                ts.name AS source_name, ts.platform AS source_platform
            FROM trend_posts tp
            JOIN trend_sources ts ON ts.id = tp.trend_source_id
            WHERE tp.created_at >= %s
            ORDER BY tp.created_at DESC
            LIMIT %s
            """,
            (since, limit),
        )

    # Metadata kesegaran data: seberapa baru postingan TERBARU yang ditemukan,
    # supaya chatbot/LLM bisa bilang eksplisit ke user seberapa update datanya
    # (mis. "data terbaru dari 2 hari lalu"), alih-alih diam-diam menyajikan
    # data basi seolah itu kondisi sekarang.
    newest_post_at = rows[0]["created_at"] if rows else None
    newest_post_age_days = (
        (datetime.now() - newest_post_at).days if newest_post_at else None
    )

    return {
        "keyword": keyword,
        "days": days,
        "count": len(rows),
        "newest_post_at": newest_post_at,
        "newest_post_age_days": newest_post_age_days,
        "results": rows,
    }


# ---------------------------------------------------------------------------
# GET /trends/live?keyword=...&geo=ID
# Beda dari GET /trends (yang search di data lama hasil scraping harian),
# endpoint ini query LANGSUNG ke Google Trends saat dipanggil. Dipakai untuk
# menjawab keyword/topik yang belum pernah didaftarkan sebagai target
# pemantauan di trend_sources -- jadi chatbot nggak dibatasi cuma ke
# kompetitor/keyword yang sudah di-setup manual di admin panel.
# ---------------------------------------------------------------------------
@app.get("/trends/live", dependencies=[Depends(verify_api_key)])
def get_trends_live(
    keyword: str = Query(..., description="Kata kunci pencarian, contoh: 'diskon lebaran'"),
    geo: str = Query(default="ID", description="Kode negara, default ID (Indonesia)"),
):
    return get_live_trend(keyword=keyword, geo=geo)


# ---------------------------------------------------------------------------
# GET /competitor/{nama}
# Detail satu target/kompetitor + postingan terbaru miliknya.
# ---------------------------------------------------------------------------
@app.get("/competitor/{nama}", dependencies=[Depends(verify_api_key)])
def get_competitor(nama: str, limit: int = Query(default=10, ge=1, le=50)):
    source = fetch_one(
        "SELECT id, name, platform, source_url, is_active FROM trend_sources WHERE name LIKE %s LIMIT 1",
        (f"%{nama}%",),
    )

    if not source:
        raise HTTPException(status_code=404, detail=f"Kompetitor/target '{nama}' tidak ditemukan")

    posts = fetch_all(
        """
        SELECT id, title, content, post_url, posted_at, created_at
        FROM trend_posts
        WHERE trend_source_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (source["id"], limit),
    )

    # Sama seperti /trends: sertakan kesegaran data eksplisit, supaya kalau
    # postingan terbaru kompetitor ini ternyata sudah lama, chatbot bisa
    # bilang jujur ke user alih-alih menyajikannya seolah aktivitas terkini.
    newest_post_at = posts[0]["created_at"] if posts else None
    newest_post_age_days = (
        (datetime.now() - newest_post_at).days if newest_post_at else None
    )

    return {
        "source": source,
        "newest_post_at": newest_post_at,
        "newest_post_age_days": newest_post_age_days,
        "recent_posts": posts,
    }


# ---------------------------------------------------------------------------
# GET /summary?days=1
# Ringkasan insight harian: aktivitas per sumber + postingan terbaru
# lintas semua kompetitor. Ini yang dipakai chatbot untuk jawab
# "apa yang lagi rame minggu ini?".
# ---------------------------------------------------------------------------
@app.get("/summary", dependencies=[Depends(verify_api_key)])
def get_summary(days: int = Query(default=1, ge=1, le=30)):
    since = datetime.now() - timedelta(days=days)

    activity_per_source = fetch_all(
        """
        SELECT
            ts.id, ts.name, ts.platform,
            COUNT(tp.id) AS post_count,
            MAX(tp.created_at) AS last_post_at
        FROM trend_sources ts
        LEFT JOIN trend_posts tp
            ON tp.trend_source_id = ts.id AND tp.created_at >= %s
        WHERE ts.is_active = 1
        GROUP BY ts.id, ts.name, ts.platform
        ORDER BY post_count DESC
        """,
        (since,),
    )

    latest_posts = fetch_all(
        """
        SELECT
            tp.id, tp.title, tp.content, tp.post_url, tp.created_at,
            ts.name AS source_name, ts.platform AS source_platform
        FROM trend_posts tp
        JOIN trend_sources ts ON ts.id = tp.trend_source_id
        WHERE tp.created_at >= %s
        ORDER BY tp.created_at DESC
        LIMIT 10
        """,
        (since,),
    )

    return {
        "period_days": days,
        "since": since.isoformat(),
        "activity_per_source": activity_per_source,
        "latest_posts": latest_posts,
    }


# ---------------------------------------------------------------------------
# POST /scrape/run
# Jalankan scraping untuk semua target aktif sekarang juga, dan kembalikan
# ringkasan berhasil/gagal per target. Dipanggil oleh scheduler harian di
# Laravel (php artisan scrape:run), dan bisa juga dipanggil manual dari admin
# panel untuk "tarik data sekarang".
# ---------------------------------------------------------------------------
@app.post("/scrape/run", dependencies=[Depends(verify_api_key)])
def trigger_scrape():
    results = run_scraper()

    # status per target sekarang bisa: "success" (data baru disimpan),
    # "unchanged" (konten sama seperti terakhir, sengaja tidak di-insert
    # duplikat -- lihat scraper.py get_last_content_hash), atau "failed".
    success = sum(1 for r in results if r["status"] == "success")
    unchanged = sum(1 for r in results if r["status"] == "unchanged")
    failed = len(results) - success - unchanged

    return {
        "total": len(results),
        "success": success,
        "unchanged": unchanged,
        "failed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# POST /meta/sync
# Tarik data TERBARU dari Meta Graph API (akun Instagram Business/Creator
# milik SR Group sendiri -- BUKAN kompetitor, lihat app/meta_insights.py)
# dan simpan/update ke tabel meta_posts & meta_account_snapshots.
#
# Dipanggil oleh scheduler Laravel (php artisan meta:sync) tiap beberapa
# menit untuk simulasikan "real-time", dan bisa juga dipanggil manual dari
# tombol "Sync Sekarang" di admin panel.
# ---------------------------------------------------------------------------
@app.post("/meta/sync", dependencies=[Depends(verify_api_key)])
def trigger_meta_sync(limit: int = Query(default=25, ge=1, le=100)):
    try:
        result = sync_meta_account(limit=limit)
        return {"status": "success", **result}
    except MetaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except MetaApiError as e:
        raise HTTPException(status_code=502, detail=f"Meta Graph API error: {e}")


# ---------------------------------------------------------------------------
# GET /meta/engagement/summary?days=7
# Ringkasan engagement akun Meta sendiri dalam N hari terakhir: rata-rata
# engagement rate, post terbaik, dan kesegaran data (data_age_minutes) --
# dibaca dari DB lokal (hasil sync terakhir), BUKAN hit Graph API langsung,
# supaya cepat & tidak boros rate limit Meta. Dipakai chatbot & dashboard.
# ---------------------------------------------------------------------------
@app.get("/meta/engagement/summary", dependencies=[Depends(verify_api_key)])
def get_meta_engagement_summary(days: int = Query(default=7, ge=1, le=90)):
    return get_engagement_summary(days=days)


# ---------------------------------------------------------------------------
# GET /meta/posts?limit=10
# Daftar post terbaru akun Meta sendiri beserta angka engagement-nya,
# dibaca dari DB lokal (hasil sync terakhir).
# ---------------------------------------------------------------------------
@app.get("/meta/posts", dependencies=[Depends(verify_api_key)])
def get_meta_posts(limit: int = Query(default=10, ge=1, le=50)):
    return {"results": get_recent_meta_posts(limit=limit)}
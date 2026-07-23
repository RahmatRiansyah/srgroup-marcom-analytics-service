from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query

from app.auth import verify_api_key
from app.db import fetch_all, fetch_one
from app.google_trends import get_live_trend
from scraper import run_scraper

load_dotenv()

app = FastAPI(
    title="SRGroup Marcom Analytics Service",
    description=(
        "Mesin analisis tren & kompetitor untuk chatbot marketing SR Group. "
        "Semua endpoint di bawah /trends, /competitor, /summary butuh header "
        "X-API-Key."
    ),
    version="0.1.0",
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
):
    if keyword:
        like = f"%{keyword}%"
        rows = fetch_all(
            """
            SELECT
                tp.id, tp.title, tp.content, tp.post_url, tp.posted_at, tp.created_at,
                ts.name AS source_name, ts.platform AS source_platform
            FROM trend_posts tp
            JOIN trend_sources ts ON ts.id = tp.trend_source_id
            WHERE tp.title LIKE %s OR tp.content LIKE %s OR ts.name LIKE %s
            ORDER BY tp.created_at DESC
            LIMIT %s
            """,
            (like, like, like, limit),
        )
    else:
        rows = fetch_all(
            """
            SELECT
                tp.id, tp.title, tp.content, tp.post_url, tp.posted_at, tp.created_at,
                ts.name AS source_name, ts.platform AS source_platform
            FROM trend_posts tp
            JOIN trend_sources ts ON ts.id = tp.trend_source_id
            ORDER BY tp.created_at DESC
            LIMIT %s
            """,
            (limit,),
        )

    return {
        "keyword": keyword,
        "count": len(rows),
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

    return {
        "source": source,
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
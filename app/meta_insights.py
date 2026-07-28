"""
meta_insights.py

Integrasi RESMI ke Meta Graph API (Instagram Graph API, akun Instagram
Business/Creator milik SR Group sendiri yang terhubung ke Facebook Page yang
dikelola tim) -- BUKAN scraping. Beda dari scraper.py/headless_scraper.py
yang narik konten kompetitor (yang memang tidak punya API resmi untuk pihak
luar), modul ini khusus untuk akun MILIK SENDIRI, jadi bisa pakai jalur resmi
& dapat angka engagement asli (like, komentar, saved, shares, reach) --
bukan cuma caption/teks seperti hasil scraping kompetitor.

ALUR:
1. fetch_account_profile()  -> ambil username, followers_count, media_count
2. fetch_recent_media()     -> daftar post terbaru (like_count, comments_count
                                dari field dasar, selalu stabil)
3. fetch_media_insights()   -> reach, saved, shares, views per post (endpoint
                                /insights -- field yang paling sering berubah
                                nama/ketersediaannya dari waktu ke waktu)
4. sync_meta_account()      -> gabungkan semua di atas, hitung engagement
                                rate, simpan ke tabel meta_posts &
                                meta_account_snapshots. Ini yang dipanggil
                                endpoint POST /meta/sync (main.py).

CATATAN PENTING soal "real-time":
Meta TIDAK menyediakan push/webhook untuk perubahan angka like/komentar/reach
secara live. "Real-time" di sini artinya: data ditarik ulang dari Graph API
secara berkala (polling, dipicu scheduler Laravel tiap N menit -- lihat
RunMetaSync di project Laravel), sehingga dashboard & chatbot selalu baca
data yang beberapa menit terakhir, bukan data kemarin/minggu lalu seperti
hasil scraping kompetitor. Field `fetched_at` di setiap row menyimpan kapan
tepatnya data itu ditarik, supaya UI/chatbot bisa jujur soal kesegarannya
(pola yang sama seperti `newest_post_age_days` di trends/summary).

CATATAN PENTING soal metric Graph API:
Meta cukup sering deprecate/ganti nama metric insights (contoh: `impressions`
& `video_views` di-deprecate mulai Graph API v22, diganti `views`). Supaya
modul ini tidak diam-diam berhenti berfungsi total kalau Meta ganti sesuatu,
daftar metric bisa dikonfigurasi lewat .env (META_MEDIA_INSIGHTS_METRICS),
dan kalau permintaan metric gagal (400 dari Graph API), otomatis fallback ke
set metric minimal (`reach` saja) alih-alih gagal total satu post itu.
Selalu cek changelog resmi kalau ada error 400 yang konsisten:
https://developers.facebook.com/docs/graph-api/changelog
"""

import os
from datetime import datetime, timedelta
from typing import Optional

import requests

from app.db import fetch_all, get_connection

GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v23.0")
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Metric level-media default. Bisa dioverride lewat .env kalau Meta mengubah
# skema ini di masa depan tanpa perlu deploy ulang kode.
DEFAULT_MEDIA_METRICS = os.getenv("META_MEDIA_INSIGHTS_METRICS", "reach,saved,shares,views")
# Fallback paling minim kalau metric di atas ditolak Graph API (400) -- lebih
# baik dapat sebagian data (reach) daripada sync satu post itu gagal total.
FALLBACK_MEDIA_METRICS = "reach"

REQUEST_TIMEOUT = 15


class MetaConfigError(Exception):
    """Dilempar kalau kredensial Meta belum diisi di .env."""


class MetaApiError(Exception):
    """Dilempar kalau Graph API mengembalikan error yang tidak bisa di-fallback."""


def _config() -> dict:
    access_token = os.getenv("META_ACCESS_TOKEN")
    ig_business_id = os.getenv("META_IG_BUSINESS_ID")

    if not access_token or not ig_business_id:
        raise MetaConfigError(
            "META_ACCESS_TOKEN dan/atau META_IG_BUSINESS_ID belum diset di .env "
            "service ini. Lihat README bagian setup Meta Developer App."
        )

    return {"access_token": access_token, "ig_business_id": ig_business_id}


def _get(path: str, params: dict) -> dict:
    """GET generik ke Graph API. Melempar MetaApiError dengan pesan Graph API
    apa adanya (bukan pesan generik) supaya gampang di-debug -- error Graph
    API biasanya sudah cukup jelas (mis. token expired, permission kurang)."""
    url = f"{GRAPH_BASE_URL}/{path}"
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    payload = response.json() if response.content else {}

    if response.status_code != 200:
        error_message = payload.get("error", {}).get("message", f"HTTP {response.status_code}")
        raise MetaApiError(error_message)

    return payload


# ---------------------------------------------------------------------------
# Profil akun (followers_count dsb -- field DASAR, bukan /insights, jadi jauh
# lebih stabil terhadap perubahan API dibanding metric insights).
# ---------------------------------------------------------------------------
def fetch_account_profile() -> dict:
    cfg = _config()
    return _get(
        cfg["ig_business_id"],
        {
            "fields": "username,followers_count,media_count",
            "access_token": cfg["access_token"],
        },
    )


# ---------------------------------------------------------------------------
# Daftar post terbaru + field dasar (like_count, comments_count -- stabil,
# bukan bagian dari /insights yang sering berubah).
# ---------------------------------------------------------------------------
def fetch_recent_media(limit: int = 25) -> list[dict]:
    cfg = _config()
    payload = _get(
        f"{cfg['ig_business_id']}/media",
        {
            "fields": (
                "id,caption,media_type,media_product_type,permalink,media_url,"
                "timestamp,like_count,comments_count"
            ),
            "limit": limit,
            "access_token": cfg["access_token"],
        },
    )
    return payload.get("data", [])


# ---------------------------------------------------------------------------
# Insight per post (reach, saved, shares, views). Ini bagian paling rawan
# berubah -- makanya dibungkus retry dengan fallback metric minimal.
# ---------------------------------------------------------------------------
def fetch_media_insights(media_id: str) -> dict:
    cfg = _config()

    try:
        payload = _get(
            f"{media_id}/insights",
            {"metric": DEFAULT_MEDIA_METRICS, "access_token": cfg["access_token"]},
        )
    except MetaApiError:
        # Kemungkinan salah satu metric di DEFAULT_MEDIA_METRICS sudah tidak
        # didukung lagi (Meta ganti skema) atau tidak berlaku untuk tipe
        # media ini (mis. "views" untuk foto statis). Coba lagi dengan metric
        # paling minimal supaya tidak kehilangan data reach sama sekali.
        try:
            payload = _get(
                f"{media_id}/insights",
                {"metric": FALLBACK_MEDIA_METRICS, "access_token": cfg["access_token"]},
            )
        except MetaApiError:
            # Post ini benar-benar tidak bisa diambil insight-nya (mis. post
            # terlalu baru, atau akun di bawah 100 followers -- batasan resmi
            # Meta). Kembalikan kosong, bukan raise, supaya media lain dalam
            # satu batch sync tetap lanjut diproses.
            return {}

    result = {}
    for item in payload.get("data", []):
        name = item.get("name")
        values = item.get("values", [])
        if name and values:
            result[name] = values[0].get("value")

    return result


def _calc_engagement(likes: int, comments: int, saved: Optional[int], shares: Optional[int],
                      reach: Optional[int], followers_count: Optional[int]) -> dict:
    interactions = likes + comments + (saved or 0) + (shares or 0)

    engagement_rate_reach = None
    if reach and reach > 0:
        engagement_rate_reach = round((interactions / reach) * 100, 2)

    engagement_rate_followers = None
    if followers_count and followers_count > 0:
        engagement_rate_followers = round((interactions / followers_count) * 100, 2)

    return {
        "interactions": interactions,
        "engagement_rate_reach": engagement_rate_reach,
        "engagement_rate_followers": engagement_rate_followers,
    }


def _upsert_meta_post(conn, media: dict, insights: dict, followers_count: Optional[int]) -> None:
    likes = media.get("like_count") or 0
    comments = media.get("comments_count") or 0
    saved = insights.get("saved")
    shares = insights.get("shares")
    reach = insights.get("reach")
    views = insights.get("views")

    calc = _calc_engagement(likes, comments, saved, shares, reach, followers_count)
    # PENTING: pakai datetime.now() naive (waktu lokal server), BUKAN
    # datetime.now(timezone.utc) -- supaya konsisten dengan seluruh konvensi
    # project ini (scraper.py, main.py, config Laravel) yang semuanya
    # menyimpan & membandingkan waktu lokal naive. Sebelumnya modul ini
    # menyimpan timestamp dalam UTC-aware tapi dibaca ulang seolah-olah waktu
    # lokal (lihat get_engagement_summary) -- bikin "data_age_minutes" salah
    # sekitar 7 jam (selisih WIB vs UTC), jadi chatbot bisa keliru bilang
    # data "berjam-jam lalu" padahal baru saja di-sync.
    now = datetime.now()

    posted_at = media.get("timestamp")
    if posted_at:
        # Format Graph API: 2026-07-20T10:00:00+0000 (selalu UTC, ditandai
        # offset +0000 secara eksplisit oleh Meta). Di-parse sebagai
        # timezone-aware dulu, LALU dikonversi ke waktu lokal naive supaya
        # konsisten dengan `now` di atas & seluruh timestamp lain di project ini.
        posted_at = datetime.strptime(posted_at, "%Y-%m-%dT%H:%M:%S%z")
        posted_at = posted_at.astimezone().replace(tzinfo=None)

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO meta_posts (
            external_media_id, media_type, media_product_type, caption, permalink,
            media_url, posted_at, likes, comments, saved, shares, reach, views,
            engagement_rate_reach, engagement_rate_followers, fetched_at,
            created_at, updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            caption = VALUES(caption),
            likes = VALUES(likes),
            comments = VALUES(comments),
            saved = VALUES(saved),
            shares = VALUES(shares),
            reach = VALUES(reach),
            views = VALUES(views),
            engagement_rate_reach = VALUES(engagement_rate_reach),
            engagement_rate_followers = VALUES(engagement_rate_followers),
            fetched_at = VALUES(fetched_at),
            updated_at = VALUES(updated_at)
        """,
        (
            media.get("id"), media.get("media_type"), media.get("media_product_type"),
            media.get("caption"), media.get("permalink"), media.get("media_url"),
            posted_at, likes, comments, saved, shares, reach, views,
            calc["engagement_rate_reach"], calc["engagement_rate_followers"],
            now, now, now,
        ),
    )
    cursor.close()


def sync_meta_account(limit: int = 25) -> dict:
    """Tarik profil akun + N post terbaru + insight masing-masing dari Graph
    API, lalu simpan/update ke tabel meta_posts & meta_account_snapshots.

    Dipanggil dari POST /meta/sync (main.py), yang pada gilirannya dipanggil
    scheduler Laravel tiap beberapa menit (lihat RunMetaSync) supaya data di
    dashboard & chatbot selalu segar tanpa harus hit Graph API di setiap
    request user (rate limit Graph API terbatas per jam).
    """
    profile = fetch_account_profile()
    followers_count = profile.get("followers_count")
    media_list = fetch_recent_media(limit=limit)

    synced = 0
    failed = 0
    engagement_rates = []

    with get_connection() as conn:
        for media in media_list:
            media_id = media.get("id")
            if not media_id:
                failed += 1
                continue

            try:
                insights = fetch_media_insights(media_id)
                _upsert_meta_post(conn, media, insights, followers_count)
                synced += 1

                likes = media.get("like_count") or 0
                comments = media.get("comments_count") or 0
                calc = _calc_engagement(
                    likes, comments, insights.get("saved"), insights.get("shares"),
                    insights.get("reach"), followers_count,
                )
                rate = calc["engagement_rate_reach"] or calc["engagement_rate_followers"]
                if rate is not None:
                    engagement_rates.append(rate)
            except Exception:
                failed += 1
                continue

        conn.commit()

        avg_engagement_rate = round(sum(engagement_rates) / len(engagement_rates), 2) if engagement_rates else None
        now = datetime.now()  # naive lokal, konsisten dengan _upsert_meta_post di atas

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO meta_account_snapshots (
                username, followers_count, media_count, avg_engagement_rate,
                snapshot_at, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                profile.get("username"), followers_count, profile.get("media_count"),
                avg_engagement_rate, now, now, now,
            ),
        )
        cursor.close()
        conn.commit()

    return {
        "username": profile.get("username"),
        "followers_count": followers_count,
        "media_count": profile.get("media_count"),
        "posts_synced": synced,
        "posts_failed": failed,
        "avg_engagement_rate": avg_engagement_rate,
        "synced_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Baca balik dari DB lokal (dipakai endpoint GET /meta/engagement/summary &
# GET /meta/posts) -- BUKAN hit Graph API lagi, supaya chatbot/dashboard
# selalu respons cepat & tidak boros kuota rate limit Meta. Data "sesegar"
# sync terakhir (lihat fetched_at per row / snapshot_at).
# ---------------------------------------------------------------------------
def get_engagement_summary(days: int = 7) -> dict:
    # Threshold dihitung di Python (waktu lokal naive), BUKAN pakai NOW()/
    # DATE_SUB bawaan MySQL -- konsisten dengan pola yang sudah dipakai di
    # main.py (get_trend_summary dkk). Kalau pakai NOW() milik MySQL, hasilnya
    # tergantung timezone session/server MySQL itu sendiri (bisa saja di-set
    # UTC secara default), yang bisa beda ~7 jam dari `posted_at` yang sudah
    # kita simpan sebagai waktu lokal naive (lihat _upsert_meta_post) --
    # berpotensi salah include/exclude post di ujung rentang N hari.
    since = datetime.now() - timedelta(days=days)

    posts = fetch_all(
        """
        SELECT id, caption, permalink, media_type, posted_at, likes, comments,
               saved, shares, reach, views, engagement_rate_reach,
               engagement_rate_followers, fetched_at
        FROM meta_posts
        WHERE posted_at >= %s
        ORDER BY posted_at DESC
        """,
        (since,),
    )

    latest_snapshot = fetch_all(
        """
        SELECT username, followers_count, media_count, avg_engagement_rate, snapshot_at
        FROM meta_account_snapshots
        ORDER BY snapshot_at DESC
        LIMIT 1
        """
    )

    rates = [p["engagement_rate_reach"] or p["engagement_rate_followers"]
             for p in posts if (p["engagement_rate_reach"] or p["engagement_rate_followers"]) is not None]
    avg_rate = round(sum(rates) / len(rates), 2) if rates else None

    best_post = max(
        posts,
        key=lambda p: (p["engagement_rate_reach"] or p["engagement_rate_followers"] or 0),
        default=None,
    )

    last_synced_at = posts[0]["fetched_at"] if posts else None
    data_age_minutes = (
        int((datetime.now() - last_synced_at).total_seconds() / 60) if last_synced_at else None
    )

    return {
        "period_days": days,
        "account": latest_snapshot[0] if latest_snapshot else None,
        "total_posts": len(posts),
        "avg_engagement_rate": avg_rate,
        "best_post": best_post,
        "last_synced_at": last_synced_at,
        "data_age_minutes": data_age_minutes,
        "posts": posts,
    }


def get_recent_meta_posts(limit: int = 10) -> list[dict]:
    return fetch_all(
        """
        SELECT id, caption, permalink, media_type, posted_at, likes, comments,
               saved, shares, reach, views, engagement_rate_reach,
               engagement_rate_followers, fetched_at
        FROM meta_posts
        ORDER BY posted_at DESC
        LIMIT %s
        """,
        (limit,),
    )
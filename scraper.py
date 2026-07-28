import hashlib
import os
import time
from datetime import datetime

import mysql.connector
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from headless_scraper import HeadlessBrowserScraper

load_dotenv()

# Platform yang butuh headless browser (JS-rendered + anti-bot) alih-alih
# requests+BeautifulSoup biasa. Dicocokkan case-insensitive terhadap kolom
# trend_sources.platform (mis. "Instagram", "TikTok").
SOCIAL_PLATFORMS = {"instagram", "tiktok"}

# 1. Konfigurasi Koneksi Database (dibaca dari .env, disamakan dgn Laravel)
db_config = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_DATABASE', 'db_marcom_analytics'),
}


def get_active_targets():
    """Membaca daftar target yang aktif dipantau dari tabel trend_sources"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, name, platform, source_url FROM trend_sources "
            "WHERE is_active = 1 AND source_url IS NOT NULL AND source_url != ''"
        )
        targets = cursor.fetchall()
        cursor.close()
        conn.close()
        return targets
    except Exception as e:
        print(f"[-] Gagal mengambil target dari database: {e}")
        return []


def get_last_content_hash(trend_source_id):
    """Ambil hash konten hasil scrape TERAKHIR untuk satu target.

    Dipakai buat dedup: kalau hasil scrape hari ini persis sama dengan yang
    terakhir tersimpan, jangan insert baris baru -- trend_posts jadi cepat
    penuh duplikat kalau tidak dicek (banyak website nggak berubah tiap hari).
    """
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT content FROM trend_posts WHERE trend_source_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (trend_source_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row or not row.get('content'):
            return None

        return hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
    except Exception as e:
        print(f"[-] Gagal cek histori konten: {e}")
        # Kalau gagal cek, anggap saja tidak ada histori -- lebih aman insert
        # (duplikat) daripada diam-diam skip padahal datanya sudah beda.
        return None


def save_trend_post(trend_source_id, title, content, post_url):
    """Menyimpan hasil scraping ke tabel trend_posts"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        query = """
            INSERT INTO trend_posts (trend_source_id, title, content, post_url, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        now = datetime.now()
        cursor.execute(query, (trend_source_id, title, content, post_url, now, now))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[+] Berhasil menyimpan data tren untuk Target ID: {trend_source_id}")
    except Exception as e:
        print(f"[-] Gagal menyimpan ke database: {e}")


def _scrape_generic(url: str) -> dict:
    """Scraping generik lewat requests+BeautifulSoup untuk halaman statis
    (Website, Google Trends, dst) yang tidak butuh render JavaScript.

    Dipertahankan sebagai jalur DEFAULT (bukan headless browser) karena jauh
    lebih ringan & cepat -- headless browser cuma dipakai untuk platform yang
    memang butuh (lihat SOCIAL_PLATFORMS & _scrape_target()).
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    response = requests.get(url, headers=headers, timeout=10)

    if response.status_code != 200:
        return {"status": "failed", "error": f"HTTP {response.status_code}"}

    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.title.string.strip() if soup.title else None

    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc and meta_desc.get('content'):
        content = meta_desc['content']
    else:
        paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 20]
        content = " ".join(paragraphs[:3]) if paragraphs else "Tidak ada teks detail yang diekstrak."

    return {"status": "success", "title": title, "content": content}


def _scrape_target(target: dict, headless: HeadlessBrowserScraper | None) -> dict:
    """Route satu target ke metode scraping yang sesuai berdasar platform-nya.

    - Instagram/TikTok -> headless browser (Playwright), lihat headless_scraper.py
    - selain itu (Website, Google Trends, dll) -> requests+BeautifulSoup generik
    """
    platform = (target.get('platform') or '').strip().lower()
    url = target['source_url']

    if platform == 'instagram' and headless is not None:
        return headless.scrape_instagram(url)

    if platform == 'tiktok' and headless is not None:
        return headless.scrape_tiktok(url)

    return _scrape_generic(url)


# Retry HANYA untuk kegagalan yang jelas-jelas bersifat SEMENTARA (network
# hiccup) -- BUKAN "asal gagal ya diulang". Diulang buta bisa lebih
# memperburuk kualitas data daripada membantu:
#   - status "blocked" (Instagram/TikTok ngarahin ke halaman login/challenge,
#     tanda anti-bot mereka sudah curiga) SENGAJA TIDAK diretry -- request
#     ulang berkali-kali malah nambah sinyal mencurigakan ke sistem mereka,
#     risiko IP scraper kena banned lebih parah, bukan cuma limit sesaat.
#   - HTTP 4xx (404 dst, termasuk 429/403 yang biasanya juga tanda
#     block/rate-limit) TIDAK diretry -- baik karena percuma (404 tetap 404),
#     maupun karena alasan yang sama seperti "blocked" di atas untuk 429/403.
#   - Yang DIRETRY: exception koneksi/timeout dari `requests`, timeout Playwright,
#     dan HTTP 5xx -- ini yang benar-benar cocok disebut "network hiccup".
MAX_RETRIES = 1  # percobaan TAMBAHAN di luar percobaan pertama (total maks 2x coba)
RETRY_BACKOFF_SECONDS = 3

TRANSIENT_REQUEST_EXCEPTIONS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


def _is_transient_failure(scraped: dict) -> bool:
    """True kalau hasil (bukan exception) ini layak dicoba ulang."""
    if scraped.get('status') != 'failed':
        # 'blocked' termasuk di sini -> sengaja TIDAK dianggap transient.
        return False

    error = (scraped.get('error') or '').lower()

    if 'timeout' in error:
        return True

    # Format error dari _scrape_generic(): "HTTP {status_code}"
    if error.startswith('http '):
        try:
            status_code = int(error.split(' ')[1])
        except (IndexError, ValueError):
            return False
        return 500 <= status_code < 600  # 5xx doang -- BUKAN 404/403/429

    return False


def _scrape_target_with_retry(target: dict, headless: HeadlessBrowserScraper | None) -> dict:
    """Bungkus _scrape_target() dengan retry TERBATAS untuk kegagalan sementara.
    Lihat komentar di atas MAX_RETRIES untuk kriteria lengkap apa yang diretry.
    """
    attempt = 0

    while True:
        try:
            scraped = _scrape_target(target, headless)
            transient = _is_transient_failure(scraped)
        except TRANSIENT_REQUEST_EXCEPTIONS as e:
            scraped = {"status": "failed", "error": f"Koneksi bermasalah: {e}"}
            transient = True
        # Exception lain (bug tak terduga dsb) SENGAJA tidak ditangkap di sini
        # -- biar tetap naik ke try/except di run_scraper() seperti sebelumnya,
        # bukan diam-diam dianggap "sementara" lalu diulang.

        if not transient or attempt >= MAX_RETRIES:
            return scraped

        attempt += 1
        print(
            f"[~] {target['name']}: kegagalan sementara ({scraped.get('error')}), "
            f"coba lagi dalam {RETRY_BACKOFF_SECONDS}s (percobaan {attempt + 1}/{MAX_RETRIES + 1})..."
        )
        time.sleep(RETRY_BACKOFF_SECONDS)


def run_scraper() -> list[dict]:
    """Jalankan scraping untuk semua target aktif.

    Return list hasil per target (dipakai endpoint POST /scrape/run supaya
    Laravel bisa mencatat & menampilkan log sukses/gagal di panel admin),
    selain tetap print ke stdout untuk run manual/CLI.
    """
    targets = get_active_targets()
    results: list[dict] = []

    if not targets:
        print("[!] Tidak ada target aktif dengan URL yang valid di database.")
        return results

    print(f"[*] Menemukan {len(targets)} target untuk diproses...\n")

    # Headless browser (Chromium) HANYA dibuka kalau memang ada target
    # Instagram/TikTok di batch ini, dan dipakai ULANG untuk semua target
    # semacam itu dalam satu run -- membuka browser baru per-target akan
    # jauh lebih lambat & boros resource.
    needs_headless = any(
        (target.get('platform') or '').strip().lower() in SOCIAL_PLATFORMS
        for target in targets
    )
    headless = HeadlessBrowserScraper().start() if needs_headless else None

    try:
        for target in targets:
            platform_label = target.get('platform') or 'unknown'
            print(f"[*] Memproses target: {target['name']} ({target['source_url']}) [{platform_label}]")
            entry = {"id": target["id"], "name": target["name"], "status": "failed", "error": None}

            try:
                scraped = _scrape_target_with_retry(target, headless)

                if scraped["status"] != "success":
                    # "blocked" (login wall/anti-bot Instagram-TikTok) tetap
                    # dihitung sebagai gagal di ringkasan (main.py), tapi
                    # pesan errornya jelas & tercatat di log admin -- bukan
                    # cuma "failed" generik yang bikin bingung.
                    entry["status"] = scraped["status"]
                    entry["error"] = scraped.get("error", "Gagal mengambil data.")
                    print(f"[-] Gagal: {entry['error']}")
                    results.append(entry)
                    continue

                title = scraped.get("title") or f"Konten dari {target['name']}"
                content = scraped.get("content") or "Tidak ada teks detail yang diekstrak."

                new_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
                last_hash = get_last_content_hash(target['id'])

                if last_hash is not None and new_hash == last_hash:
                    entry["status"] = "unchanged"
                    print(f"[=] Konten {target['name']} sama seperti terakhir, dilewati (tidak insert duplikat).")
                else:
                    save_trend_post(target['id'], title, content, target['source_url'])
                    entry["status"] = "success"

            except Exception as e:
                entry["error"] = str(e)
                print(f"[-] Error saat scraping {target['name']}: {e}")

            results.append(entry)
    finally:
        if headless is not None:
            headless.close()

    return results


if __name__ == "__main__":
    run_scraper()
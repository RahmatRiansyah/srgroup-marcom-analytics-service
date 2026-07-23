import hashlib
import os
from datetime import datetime

import mysql.connector
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

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

    for target in targets:
        print(f"[*] Memproses target: {target['name']} ({target['source_url']})")
        entry = {"id": target["id"], "name": target["name"], "status": "failed", "error": None}

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(target['source_url'], headers=headers, timeout=10)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                title = soup.title.string.strip() if soup.title else f"Konten dari {target['name']}"

                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc and meta_desc.get('content'):
                    content = meta_desc['content']
                else:
                    paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 20]
                    content = " ".join(paragraphs[:3]) if paragraphs else "Tidak ada teks detail yang diekstrak."

                new_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
                last_hash = get_last_content_hash(target['id'])

                if last_hash is not None and new_hash == last_hash:
                    entry["status"] = "unchanged"
                    print(f"[=] Konten {target['name']} sama seperti terakhir, dilewati (tidak insert duplikat).")
                else:
                    save_trend_post(target['id'], title, content, target['source_url'])
                    entry["status"] = "success"
            else:
                entry["error"] = f"HTTP {response.status_code}"
                print(f"[-] Gagal mengakses URL. Status Code: {response.status_code}")

        except Exception as e:
            entry["error"] = str(e)
            print(f"[-] Error saat scraping {target['name']}: {e}")

        results.append(entry)

    return results


if __name__ == "__main__":
    run_scraper()
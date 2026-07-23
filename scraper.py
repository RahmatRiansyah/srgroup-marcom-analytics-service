import mysql.connector
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 1. Konfigurasi Koneksi Database (Disesuaikan dengan .env Laravel kamu)
db_config = {
    'host': '127.0.0.1',
    'port': 3307, # Menggunakan Port 3307 sesuai .env
    'user': 'root',
    'password': '',
    'database': 'db_marcom_analytics'
}

def get_active_targets():
    """Membaca daftar target dari tabel trend_sources"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, platform, source_url FROM trend_sources WHERE source_url IS NOT NULL AND source_url != ''")
        targets = cursor.fetchall()
        cursor.close()
        conn.close()
        return targets
    except Exception as e:
        print(f"[-] Gagal mengambil target dari database: {e}")
        return []

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

def run_scraper():
    targets = get_active_targets()
    if not targets:
        print("[!] Tidak ada target dengan URL yang valid di database.")
        return

    print(f"[*] Menemukan {len(targets)} target untuk diproses...\n")

    for target in targets:
        print(f"[*] Memproses target: {target['name']} ({target['source_url']})")
        
        try:
            # Mengambil konten web (Simple Web Scraping)
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(target['source_url'], headers=headers, timeout=10)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Mengambil judul halaman / meta deskripsi sebagai contoh data tren
                title = soup.title.string.strip() if soup.title else f"Konten dari {target['name']}"
                
                # Ambil beberapa paragraf atau meta description
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc and meta_desc.get('content'):
                    content = meta_desc['content']
                else:
                    paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 20]
                    content = " ".join(paragraphs[:3]) if paragraphs else "Tidak ada teks detail yang diekstrak."

                # Simpan ke DB
                save_trend_post(target['id'], title, content, target['source_url'])
            else:
                print(f"[-] Gagal mengakses URL. Status Code: {response.status_code}")

        except Exception as e:
            print(f"[-] Error saat scraping {target['name']}: {e}")

if __name__ == "__main__":
    run_scraper()
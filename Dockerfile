FROM python:3.11-slim

WORKDIR /app

# Dependency dulu biar layer cache Docker kepakai kalau requirements.txt tidak berubah
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium headless + library OS yang dibutuhkannya (untuk headless_scraper.py
# yang scraping Instagram/TikTok). --with-deps otomatis install dependency
# sistem (fonts, libnss3, dll) lewat apt, jadi image tetap bisa jalan di
# base image slim tanpa perlu daftar paket manual satu-satu.
RUN playwright install --with-deps chromium

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8001

# Railway/Render mengisi $PORT otomatis; default ke 8001 untuk run lokal via docker run.
# UVICORN_WORKERS: default 2 proses -- cukup untuk mulai manfaatkan multi-core
# tanpa langsung boros memori (tiap worker python terpisah, dan headless_scraper.py
# bisa spawn Chromium per proses saat scraping). Naikkan sesuai jumlah core &
# memori server, dan samakan DB_POOL_SIZE (lihat app/db.py) supaya total
# koneksi (worker x pool_size) tidak melebihi max_connections MySQL.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001} --workers ${UVICORN_WORKERS:-2}"]

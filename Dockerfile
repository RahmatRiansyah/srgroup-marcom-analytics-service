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

# Railway/Render mengisi $PORT otomatis; default ke 8001 untuk run lokal via docker run
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001}"]

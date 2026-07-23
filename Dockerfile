FROM python:3.11-slim

WORKDIR /app

# Dependency dulu biar layer cache Docker kepakai kalau requirements.txt tidak berubah
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8001

# Railway/Render mengisi $PORT otomatis; default ke 8001 untuk run lokal via docker run
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001}"]

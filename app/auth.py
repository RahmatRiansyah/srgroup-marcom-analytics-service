import os

from fastapi import Header, HTTPException, status


def verify_api_key(x_api_key: str = Header(default=None)) -> None:
    """Dependency sederhana untuk mengamankan komunikasi Laravel <-> Python.

    Laravel wajib mengirim header:  X-API-Key: <ANALYTICS_API_KEY>
    Set ANALYTICS_API_KEY di .env service ini, dan samakan nilainya
    dengan ANALYTICS_API_KEY di .env Laravel.
    """
    expected_key = os.getenv("ANALYTICS_API_KEY")

    if not expected_key:
        # Kalau belum dikonfigurasi, jangan diam-diam meloloskan request.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ANALYTICS_API_KEY belum diset di server .env",
        )

    if not x_api_key or x_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key tidak valid atau tidak dikirim (header X-API-Key)",
        )

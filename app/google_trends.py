"""
Live Google Trends lookup lewat pytrends.

Beda dengan trend_posts (isinya hasil scraping harian dari trend_sources yang
sudah didaftarkan admin), modul ini query LANGSUNG ke Google Trends saat
endpoint dipanggil -- jadi bisa jawab keyword/topik yang belum pernah
didaftarkan sama sekali sebagai target pemantauan.
"""
from __future__ import annotations

from pytrends.request import TrendReq


def get_live_trend(keyword: str, geo: str = "ID", timeframe: str = "now 7-d") -> dict:
    """Ambil interest over time + related/rising queries untuk satu keyword.

    Bisa gagal kalau Google Trends lagi rate-limit (umum terjadi kalau
    dipanggil terlalu sering dalam waktu singkat) -- caller WAJIB menangani
    key 'error' di hasil, bukan asumsikan selalu sukses.
    """
    try:
        pytrends = TrendReq(hl="id-ID", tz=420, retries=2, backoff_factor=0.5)
        pytrends.build_payload([keyword], cat=0, timeframe=timeframe, geo=geo)

        interest_points = []
        interest_df = pytrends.interest_over_time()
        if not interest_df.empty:
            for ts, row in interest_df.iterrows():
                interest_points.append(
                    {"date": ts.strftime("%Y-%m-%d"), "value": int(row[keyword])}
                )

        related = pytrends.related_queries().get(keyword, {}) or {}
        top_related = related.get("top")
        rising_related = related.get("rising")

        return {
            "error": False,
            "keyword": keyword,
            "geo": geo,
            "timeframe": timeframe,
            "interest_over_time": interest_points,
            "top_related_queries": top_related.to_dict("records") if top_related is not None else [],
            "rising_related_queries": rising_related.to_dict("records") if rising_related is not None else [],
        }
    except Exception as e:
        # pytrends bisa lempar macam-macam: rate limit dari Google (paling
        # umum), parsing error, timeout, dsb. Jangan biarkan endpoint 500 --
        # kembalikan pesan yang bisa dibaca & dijelaskan ulang oleh LLM ke user.
        return {
            "error": True,
            "keyword": keyword,
            "message": f"Gagal mengambil data Google Trends langsung: {e}",
        }
"""
headless_scraper.py

Modul scraping berbasis headless browser (Playwright/Chromium) khusus untuk
platform media sosial yang kontennya di-render lewat JavaScript dan aktif
mendeteksi bot -- requests+BeautifulSoup biasa (scraper.py generik, dipakai
untuk target "Website" biasa) sering gagal atau dipaksa redirect ke halaman
login untuk platform seperti ini.

Dipakai HANYA untuk target dengan platform Instagram/TikTok (lihat routing
di scraper.py -> run_scraper()); target Website/lainnya tetap lewat
requests+BeautifulSoup yang sudah ada karena jauh lebih ringan & cukup untuk
halaman statis.

CATATAN PENTING (batasan yang jujur, bukan janji "pasti berhasil"):
- Instagram & TikTok AKTIF melawan scraping (deteksi bot, rate limit, wall
  login/challenge). Modul ini hanya mengambil apa yang tersedia PUBLIK tanpa
  login (meta tag og:title/og:description, dan untuk TikTok state JSON yang
  di-embed di HTML) -- BUKAN bypass otentikasi atau anti-bot yang lebih canggih.
- Kalau halaman diarahkan ke login/challenge, fungsi ini mengembalikan status
  "blocked" dengan pesan jelas, BUKAN pura-pura sukses dengan data kosong --
  supaya log scraping tetap jujur & gampang ditelusuri lewat halaman admin.
- Untuk pemakaian produksi yang butuh keandalan tinggi & data lebih lengkap
  (jumlah like/komentar, dst), pertimbangkan API resmi (Instagram Graph API /
  TikTok Research API/Display API) yang didukung & dijamin oleh platform.
"""

import json
import re
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

NAV_TIMEOUT_MS = 20000

# Ditutup best-effort kalau muncul (cookie consent, popup "buka app", dsb).
# Tidak melempar error kalau tombolnya tidak ketemu -- ini cuma percobaan.
DIALOG_DISMISS_TEXTS = ["Allow all cookies", "Decline optional cookies", "Not Now", "Tutup", "Terima"]

LOGIN_WALL_MARKERS = ["/accounts/login", "/login", "/challenge", "/checkpoint"]


class HeadlessBrowserScraper:
    """Wrapper Playwright yang dipakai ULANG untuk banyak target dalam satu
    kali run scraping. Browser sengaja HANYA dibuka sekali (bukan per-target)
    supaya hemat resource & waktu -- pakai lewat context manager:

        with HeadlessBrowserScraper() as hs:
            hasil = hs.scrape_instagram(url)
            hasil2 = hs.scrape_tiktok(url2)
    """

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright = None
        self._browser = None

    def __enter__(self) -> "HeadlessBrowserScraper":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def start(self) -> "HeadlessBrowserScraper":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def _new_page(self):
        context = self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="id-ID",
            timezone_id="Asia/Jakarta",
        )
        page = context.new_page()
        page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        page.set_default_timeout(NAV_TIMEOUT_MS)
        return context, page

    # ------------------------------------------------------------------
    # Instagram
    # ------------------------------------------------------------------
    def scrape_instagram(self, url: str) -> dict:
        """Ambil title & bio/caption publik dari satu profil/post Instagram.

        Instagram me-render og:title & og:description di server (SSR) untuk
        halaman publik, jadi biasanya bisa langsung dibaca tanpa perlu
        scroll/interaksi lanjutan -- tapi banyak URL (khususnya post
        spesifik) tetap bisa dipaksa redirect ke halaman login kalau
        IP/browser dianggap mencurigakan oleh sistem anti-bot mereka.
        """
        context, page = self._new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            self._dismiss_common_dialogs(page)

            if self._looks_like_login_wall(page):
                return {
                    "status": "blocked",
                    "error": "Instagram mengarahkan ke halaman login/challenge (konten tidak bisa diakses tanpa login).",
                }

            title = (page.title() or "").strip()
            description = self._meta_content(page, "og:description") or self._meta_content(page, "description")

            if not description:
                return {
                    "status": "blocked",
                    "error": "Tidak ada meta deskripsi yang bisa diambil (kemungkinan halaman diblokir/di-throttle Instagram).",
                }

            return {
                "status": "success",
                "title": title or f"Instagram - {url}",
                "content": description.strip(),
            }
        except PlaywrightTimeoutError:
            return {"status": "failed", "error": "Timeout saat memuat halaman Instagram."}
        except PlaywrightError as e:
            return {"status": "failed", "error": f"Playwright error: {e}"}
        finally:
            context.close()

    # ------------------------------------------------------------------
    # TikTok
    # ------------------------------------------------------------------
    def scrape_tiktok(self, url: str) -> dict:
        """Ambil title & bio/caption publik dari satu profil/video TikTok.

        TikTok server-render sebuah blok <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">
        berisi JSON state halaman (termasuk bio profil / caption video) --
        kalau ketemu, ini dipakai lebih dulu karena lebih terstruktur & lebih
        tahan terhadap perubahan tampilan dibanding sekadar meta tag. Kalau
        tidak ketemu, fallback ke og:title/og:description seperti Instagram.
        """
        context, page = self._new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            self._dismiss_common_dialogs(page)

            if self._looks_like_login_wall(page):
                return {
                    "status": "blocked",
                    "error": "TikTok mengarahkan ke halaman login/verifikasi (konten tidak bisa diakses tanpa login).",
                }

            structured = self._extract_tiktok_state(page)
            if structured:
                return {"status": "success", **structured}

            title = (page.title() or "").strip()
            description = self._meta_content(page, "og:description") or self._meta_content(page, "description")

            if not description:
                return {
                    "status": "blocked",
                    "error": "Tidak ada data yang bisa diambil (kemungkinan halaman diblokir/di-throttle TikTok).",
                }

            return {
                "status": "success",
                "title": title or f"TikTok - {url}",
                "content": description.strip(),
            }
        except PlaywrightTimeoutError:
            return {"status": "failed", "error": "Timeout saat memuat halaman TikTok."}
        except PlaywrightError as e:
            return {"status": "failed", "error": f"Playwright error: {e}"}
        finally:
            context.close()

    # ------------------------------------------------------------------
    # Helper internal
    # ------------------------------------------------------------------
    @staticmethod
    def _meta_content(page, name: str) -> Optional[str]:
        try:
            selector = f'meta[property="{name}"], meta[name="{name}"]'
            el = page.query_selector(selector)
            return el.get_attribute("content") if el else None
        except Exception:
            return None

    @staticmethod
    def _dismiss_common_dialogs(page) -> None:
        for text in DIALOG_DISMISS_TEXTS:
            try:
                button = page.get_by_text(text, exact=False)
                if button.count() > 0:
                    button.first.click(timeout=2000)
            except Exception:
                continue

    @staticmethod
    def _looks_like_login_wall(page) -> bool:
        current_url = page.url.lower()
        return any(marker in current_url for marker in LOGIN_WALL_MARKERS)

    @staticmethod
    def _extract_tiktok_state(page) -> Optional[dict]:
        try:
            raw = page.eval_on_selector(
                "#__UNIVERSAL_DATA_FOR_REHYDRATION__",
                "el => el.textContent",
            )
        except Exception:
            raw = None

        if not raw:
            return None

        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None

        # Struktur JSON TikTok cukup dalam & bisa berubah antar versi rilis
        # mereka, jadi dicari secara defensif lewat regex di representasi
        # string-nya alih-alih hardcode satu path key yang spesifik/rapuh.
        text_blob = json.dumps(data)

        desc_match = re.search(r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"', text_blob)
        nickname_match = re.search(r'"nickname"\s*:\s*"((?:[^"\\]|\\.)*)"', text_blob)
        signature_match = re.search(r'"signature"\s*:\s*"((?:[^"\\]|\\.)*)"', text_blob)

        content_parts = []
        if desc_match:
            content_parts.append(desc_match.group(1))
        if signature_match:
            content_parts.append(signature_match.group(1))

        if not content_parts:
            return None

        title = nickname_match.group(1) if nickname_match else "TikTok"
        content = " | ".join(
            part.replace("\\n", " ").strip() for part in content_parts if part.strip()
        )

        if not content:
            return None

        return {"title": title, "content": content}

# Setup Meta Graph API (Instagram Engagement Real Data)

Panduan ini untuk mengaktifkan fitur **Performa Meta** (engagement asli: like,
komentar, saved, shares, reach dari akun Instagram Business/Creator **milik
SR Group sendiri**) di dashboard & chatbot.

> Ini BUKAN scraping — ini jalur resmi Meta Graph API. Konsekuensinya: cuma
> bisa dipakai untuk akun yang **kamu sendiri kelola** (tidak bisa untuk
> memantau akun kompetitor).

---

## Yang kamu butuhkan sebelum mulai

- [ ] Akun Instagram brand (misal `@holliday_semarang`) sudah jadi
      **Business** atau **Creator account** (bukan akun personal biasa).
      Cek di app Instagram: Settings → Account type and tools.
- [ ] Akun Instagram itu **sudah terhubung** ke sebuah **Facebook Page**
      (Page resmi brand-nya) yang kamu sendiri adalah admin-nya.
- [ ] Akun Facebook pribadi kamu (yang jadi admin Page tsb) siap dipakai
      login ke Meta for Developers.

Kalau salah satu belum terpenuhi, urus dulu bagian itu (biasanya lewat
Instagram app → Settings → Business tools and controls → Connect account
ke Page Facebook) sebelum lanjut ke langkah di bawah.

---

## Langkah 1 — Buat Meta Developer App

1. Buka **https://developers.facebook.com/apps** (login pakai akun FB admin Page).
2. Klik **Create App**.
3. Pilih tipe **Business** → lanjutkan.
4. Isi nama app bebas, misal `SR Group Marcom Analytics`.
5. Setelah app dibuat, dari dashboard app-nya, klik **Add Product** →
   cari **Instagram** → klik **Set Up** (ini menambahkan produk
   "Instagram Graph API" ke app kamu).

## Langkah 2 — Hubungkan Page & ambil izin (permission)

1. Masih di dashboard app, buka **Instagram → API setup with Instagram
   Business login** (atau menu serupa — tampilan Meta kadang berubah,
   cari yang berkaitan dengan "Business Login" / "Generate access token").
2. Pilih Facebook Page brand kamu saat diminta menghubungkan.
3. Pastikan izin (permission) berikut dicentang saat proses otorisasi:
   - `instagram_basic`
   - `pages_show_list`
   - `pages_read_engagement`
   - `instagram_manage_insights`

Kalau ada opsi pemilihan aset (Page/Instagram account), pastikan yang
dipilih adalah Page & akun Instagram brand yang benar (bukan akun lain
kalau kamu admin beberapa Page).

## Langkah 3 — Generate access token (lewat Graph API Explorer)

1. Buka **https://developers.facebook.com/tools/explorer/**.
2. Di pojok kanan atas, pilih app yang barusan dibuat (`SR Group Marcom Analytics`).
3. Di bagian **User or Page**, pilih **Get Token → Get User Access Token**.
4. Centang permission yang sama seperti Langkah 2 di atas.
5. Klik **Generate Access Token**, lalu login/konfirmasi kalau diminta.
6. Copy token yang muncul (ini token **jangka pendek**, cuma valid ~1-2 jam —
   akan ditukar jadi token jangka panjang di Langkah 5).

## Langkah 4 — Ambil `META_IG_BUSINESS_ID`

Masih di Graph API Explorer, dengan token dari Langkah 3 aktif:

1. Di kolom query (dekat tombol "Submit"), ketik:
   ```
   me/accounts?fields=name,instagram_business_account
   ```
2. Klik **Submit**. Hasilnya berupa daftar Page yang kamu kelola, contoh:
   ```json
   {
     "data": [
       {
         "name": "Holliday Semarang",
         "instagram_business_account": { "id": "17841400000000000" },
         "id": "1234567890"
       }
     ]
   }
   ```
3. Cari Page brand kamu, lalu **copy nilai `instagram_business_account.id`**
   — inilah nilai untuk `META_IG_BUSINESS_ID`.

Kalau field `instagram_business_account` tidak muncul sama sekali untuk
Page kamu, artinya Instagram belum benar-benar terhubung ke Page tsb (balik
ke prasyarat di atas).

## Langkah 5 — Tukar ke token jangka panjang (long-lived, ~60 hari)

Token dari Langkah 3 cuma tahan 1-2 jam, tidak praktis untuk sync otomatis
tiap 30 menit selama berbulan-bulan. Tukar dulu ke versi jangka panjang:

1. Ambil **App ID** & **App Secret**: dashboard app → **Settings → Basic**.
   (App Secret perlu klik "Show", mungkin diminta masukkan password FB lagi.)
2. Buka URL berikut di browser (ganti bagian `{...}` sesuai punya kamu):
   ```
   https://graph.facebook.com/v23.0/oauth/access_token?grant_type=fb_exchange_token&client_id={APP_ID}&client_secret={APP_SECRET}&fb_exchange_token={TOKEN_DARI_LANGKAH_3}
   ```
3. Responsnya berupa JSON `{"access_token": "...", "token_type": "bearer", "expires_in": 5183944}`
   — nilai `access_token` inilah yang dipakai untuk `META_ACCESS_TOKEN`.
   (`expires_in` dalam detik, ~60 hari.)

> ⚠️ **Token ini akan EXPIRED dalam ~60 hari.** Meta tidak auto-perpanjang.
> Sebelum expired, ulangi Langkah 3 & 5 untuk dapat token baru, lalu update
> `META_ACCESS_TOKEN` di `.env`. Kalau nanti dirasa merepotkan, tim bisa
> pertimbangkan setup **System User token** (App milik Business Manager,
> token bisa non-expiring) — di luar cakupan panduan dasar ini.

## Langkah 6 — Isi ke `.env`

Buka `.env` di folder `srgroup-marcom-analytics-service` (bukan punya
Laravel), isi:

```
META_ACCESS_TOKEN=isi-dengan-token-dari-langkah-5
META_IG_BUSINESS_ID=isi-dengan-id-dari-langkah-4
```

Dua variabel lain (`META_GRAPH_API_VERSION`, `META_MEDIA_INSIGHTS_METRICS`)
sudah ada nilai default yang wajar di `.env.example` — tidak wajib diubah
kecuali Meta mengganti skema API-nya (lihat komentar di `.env.example`).

## Langkah 7 — Restart service Python & tes

1. Restart `uvicorn` (supaya baca `.env` yang baru diisi):
   ```powershell
   uvicorn app.main:app --reload --port 8001
   ```
2. Di aplikasi Laravel, buka **Admin → Performa Meta**.
3. Klik **Sync Sekarang**.
4. Cek terminal `uvicorn` & `queue:work` — kalau berhasil, akan muncul log
   sync tanpa error, dan halaman Performa Meta akan menampilkan data
   followers/post/engagement asli setelah refresh.

---

## Troubleshooting

| Error | Kemungkinan penyebab |
|---|---|
| `META_ACCESS_TOKEN dan/atau META_IG_BUSINESS_ID belum diset` | `.env` di service Python belum diisi, atau `uvicorn` belum di-restart setelah diisi. |
| `Invalid OAuth access token` / `Error validating access token` | Token sudah expired (>60 hari) — ulangi Langkah 3 & 5. |
| `Unsupported request - object with ID '...' does not exist` | `META_IG_BUSINESS_ID` salah/typo, atau akun Instagram belum terhubung ke Page yang benar. |
| `(#100) Object does not exist, cannot be loaded due to missing permission` | Permission di Langkah 2/3 kurang lengkap — pastikan `instagram_manage_insights` ikut dicentang. |
| Sync sukses tapi `posts_synced: 0` | Akun tidak punya post, atau followers akun < 100 (beberapa metric insight dibatasi Meta untuk akun sangat kecil). |

Kalau error di luar tabel ini, cek pesan lengkapnya di `storage/logs/laravel.log`
(Laravel) — pesan error asli dari Graph API biasanya ikut ditampilkan di situ,
bukan pesan generik.

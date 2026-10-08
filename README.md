# Zahir CoreBridge – versi web (Vercel)

```
app.py              API Flask (Vercel mendeteksi `app = Flask(...)` otomatis)
core.py             logika inti (sama dengan desktop/core.py)
public/index.html   halaman web; public/ dilayani CDN Vercel
public/logo.png, favicon.png, icon-64.png
requirements.txt    dependensi Python
vercel.json         batas waktu fungsi 60 detik + header keamanan
```

`index.html` harus tetap di dalam `public/`. `app.py` hanya melayani alamat `/api/...`.

## Deploy

**Lewat GitHub:** push repo, lalu di Vercel pilih *Add New → Project*, impor repo, dan isi
**Root Directory = `vercel`** (bila repo berisi folder desktop juga). Framework terdeteksi Flask; tidak perlu build command.

**Lewat CLI** (dijalankan dari folder ini):

```bash
npm i -g vercel
vercel          # preview
vercel --prod   # produksi
```

**Coba lokal:**

```bash
pip install -r requirements.txt
python app.py      # buka http://localhost:5000
```

## Environment variables (opsional)

| Nama | Isi |
| --- | --- |
| `ALLOWED_SERVERS` | Akhiran domain server Zahir yang boleh dihubungi, dipisah koma. Default `zahironline.com,zahirerp.com`. |
| `ZAHIR_CLIENT_B64` | Mengganti client OAuth bawaan (base64 `client_id:client_secret`). Disarankan diisi di Vercel lalu nilai bawaannya dihapus dari `core.py` bila repo publik. |
| `ZAHIR_CLIENT_<HOST>` | Client khusus per server, titik diganti `_`, mis. `ZAHIR_CLIENT_DEMO_ZAHIRERP_COM`. |
| `FIREBASE_API_KEY` | Mengaktifkan cek lisensi online (Firestore `coretax_licenses/<slug>`). |

## Beda dengan versi desktop

- Hanya server HTTPS publik yang ada di `ALLOWED_SERVERS`; server on-premise / IP lokal tidak terjangkau dari Vercel.
- Token Zahir hanya di memori tab: refresh halaman = login ulang. Password tidak disimpan.
- Pengaturan (NPWP, kode default, kode registrasi per database) disimpan di `localStorage` browser pengguna.
- Lisensi diperiksa di server; `/api/preview` dan `/api/export` menolak database yang belum teregistrasi.
- Satu permintaan maksimal 60 detik (`vercel.json`); perkecil periode bila data sangat besar.

## API

| Endpoint | Fungsi |
| --- | --- |
| `GET /api/config` | Referensi kode transaksi, keterangan tambahan, cap fasilitas, kolom tabel |
| `POST /api/login` | `{server,email,password}` → token + daftar database |
| `POST /api/connect` | Uji koneksi + status lisensi |
| `POST /api/license` | Cek kode registrasi |
| `POST /api/customers` | Daftar customer |
| `POST /api/preview` | Faktur + Detail + peringatan + total |
| `POST /api/export/xlsx`, `/api/export/xml` | Unduh file impor Coretax |
| `POST /api/diagnose` | Tes akses per tabel |
| `POST /api/refs/parse` | Baca template DJP (.xlsx) |

Selain `config`, `login`, dan `license`, semua endpoint butuh header `Authorization: Bearer <token Zahir>`.

"""Zahir Coretax - versi web untuk Vercel (Flask).

Semua logika (query, normalisasi, export, lisensi) memakai core.py yang sama dengan aplikasi desktop.
Browser menyimpan token Zahir di sessionStorage dan mengirimnya di header `Authorization: Bearer <token>`;
password tidak pernah disimpan. Client OAuth Zahir (CLIENT_B64) hanya ada di server.
"""
import datetime as dt
import ipaddress
import os
import re

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory, redirect, abort
from werkzeug.exceptions import HTTPException

import core

app = Flask(__name__)
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024          # unggah template referensi maks 5 MB

# Server Zahir yang boleh dihubungi dari Vercel (mencegah fungsi dipakai untuk memanggil host sembarang).
# Isi env ALLOWED_SERVERS dengan akhiran domain dipisah koma, mis. "zahironline.com,zahirerp.com,erp.perusahaan.co.id".
ALLOWED = [s.strip().lower().lstrip(".") for s in
           os.environ.get("ALLOWED_SERVERS", "zahironline.com,zahirerp.com").split(",") if s.strip()]
if os.environ.get("FIREBASE_API_KEY"):
    core.FIREBASE_API_KEY = os.environ["FIREBASE_API_KEY"]
core.CLIENT_B64 = (core.CLIENT_B64 or "").strip()
# Client situs go.zahironline.com (ZahirOnline-GO): client_id sama dengan bawaan, secret beda satu digit.
# Sama seperti repo zahir-report: login mencoba GO dulu, lalu bawaan, bila Zahir membalas 401.
GO_CLIENT_B64 = "NjljYjNlYTMxN2EzMmM0ZTYxNDNlNjY1ZmRiMjBiMTQ6MTEyNmQ1MWQyZTkwZjg="
CLIENT_FROM_ENV = bool((os.environ.get("ZAHIR_CLIENT_B64") or "").strip())
CLIENTS = {}                                                  # client khusus per host: env ZAHIR_CLIENT_<HOST>
for k, v in os.environ.items():
    if k.startswith("ZAHIR_CLIENT_") and k != "ZAHIR_CLIENT_B64":
        CLIENTS[k[len("ZAHIR_CLIENT_"):].lower().replace("_", ".")] = v.strip()


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg); self.code = code


@app.errorhandler(ApiError)
def _api_error(ex):
    return jsonify(error=str(ex)), ex.code


@app.errorhandler(Exception)
def _any_error(ex):
    if isinstance(ex, HTTPException):
        return jsonify(error=ex.description), ex.code
    msg = core.friendly(f"{type(ex).__name__}: {ex}")
    code = 401 if "HTTP 401" in msg else 403 if "HTTP 403" in msg else 502
    return jsonify(error=msg), code


def _client_id(b64):
    """client_id (bagian sebelum ':') dari header client; secret tidak pernah ditampilkan."""
    import base64
    try: return base64.b64decode(b64).decode().split(":")[0]
    except Exception: return "(tidak valid)"


def server_of(raw):
    """Validasi alamat server dari browser -> (protocol, host)."""
    proto, host = core.parse_server(raw)
    name = host.split(":")[0].lower()
    try:
        ipaddress.ip_address(name); raise ApiError("Alamat IP tidak diizinkan di versi web. Gunakan aplikasi desktop.")
    except ValueError:
        pass
    if name in ("localhost",) or not any(name == s or name.endswith("." + s) for s in ALLOWED):
        raise ApiError(f"Server {name} tidak ada di daftar yang diizinkan ({', '.join(ALLOWED)}). "
                       "Tambahkan lewat env ALLOWED_SERVERS di Vercel.")
    if proto != "https":
        raise ApiError("Versi web hanya mendukung server HTTPS.")
    return proto, host


def body():
    return request.get_json(silent=True) or {}


def conn(b):
    """Data koneksi zsql dari header token + body (server, slug)."""
    auth = request.headers.get("Authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not tok: raise ApiError("Sesi berakhir. Silakan login ulang.", 401)
    slug = str(b.get("slug") or "").strip().lower()
    if not re.fullmatch(r"[\w.-]{3,120}", slug): raise ApiError("Database belum dipilih.")
    proto, host = server_of(b.get("server"))
    return {"token": tok, "slug": slug, "protocol": proto.upper(), "server": host, "url": f"{proto}://{host}/api/v2/zsql"}


def need_license(slug, code):
    ok, msg, exp = core.license_state(slug, code)
    if not ok: raise ApiError(f"Database ini {msg}. Masukkan kode registrasi dari admin.", 403)
    return exp


def job(b):
    """Parameter fetch() dari form web."""
    f = conn(b)
    need_license(f["slug"], b.get("license"))
    ids = [str(x) for x in (b.get("cust_ids") or [])][:2000]
    f.update(d1=str(b.get("d1") or ""), d2=str(b.get("d2") or ""), npwp=str(b.get("npwp") or ""),
             kode_a=str(b.get("kode_a") or ""), kode_b=str(b.get("kode_b") or ""),
             kode_trx=str(b.get("kode_trx") or "04"), ket_tambahan=str(b.get("ket") or ""),
             cap_fasilitas=str(b.get("cap") or ""), cust_ids=ids)
    return f


def clean(rows):
    out = []
    for r in rows:
        d = {k: v for k, v in r.items() if not k.startswith("_")}
        if r.get("_kode_default"): d["kode_default"] = True        # untuk penanda kode default di tabel
        out.append(d)
    return out



# ------------------------------------------------------------------ daftar perusahaan (halaman "Pilih Perusahaan")
def _pick(o, keys, depth=0, seen=None):
    """Nilai skalar pertama untuk salah satu `keys`, dicari sampai kedalaman 4 (sama dengan zahir-report)."""
    seen = set() if seen is None else seen
    if not isinstance(o, (dict, list)) or depth > 4 or id(o) in seen: return None
    seen.add(id(o))
    if isinstance(o, dict):
        for k in keys:
            v = o.get(k)
            if v is not None and not isinstance(v, (dict, list)): return v
        vals = list(o.values())
    else:
        vals = o
    for v in vals:
        r = _pick(v, keys, depth + 1, seen)
        if r is not None: return r
    return None


def _norm_status(v):
    k = re.sub(r"[^a-z]", "", str(v if v is not None else "").lower())
    if not k: return ""
    if re.search(r"inactive|notactive|nonactive|nonaktif|tidakaktif|disabled|suspend|deactiv", k): return "INACTIVE"
    if "postsubscri" in k: return "Post Subscribe"
    if "subscri" in k or "active" in k: return "Active"
    if "trial" in k: return "Trial"
    if "expire" in k: return "Expired"
    if "await" in k or "payment" in k: return "Awaiting Payment"
    return str(v)


def _company_row(c, host):
    co = c.get("company") if isinstance(c.get("company"), dict) else {}
    mem = c.get("membership") if isinstance(c.get("membership"), dict) else (co.get("membership") if isinstance(co.get("membership"), dict) else {})
    slugs = core._find_slugs(c, host) or [x for x in [core._find_key(c, "slug")] if x]
    if not slugs: return None
    status = _norm_status(mem.get("status") if mem.get("status") is not None else _pick(c, ["membership_status", "status"]))
    keys = ["expired_at", "expired_date", "expiry_date", "expire_at", "expired", "end_date"]
    exp = _pick(mem, keys)
    if exp is None: exp = _pick(c, keys)
    exp = "" if exp is None else str(exp)
    try:                                                    # tanggal berakhir sudah lewat -> Expired
        if exp and status != "INACTIVE" and dt.date.fromisoformat(exp[:10]) < dt.date.today(): status = "Expired"
    except ValueError:
        pass
    def txt(keys): v = _pick(c, keys); return "" if v is None else str(v)
    return {"name": str(co.get("name") or c.get("name") or c.get("company_name") or c.get("title") or slugs[0]),
            "slug": slugs[0], "version": txt(["version", "product_version", "app_version", "api_version"]),
            "edition": txt(["edition", "product_type", "product", "package", "variant_name"]),
            "expired": exp, "status": status}


def companies_full(tok, proto, host, clients, me=None):
    """Perusahaan milik user + versi/edisi/kedaluarsa/status. Mencoba tiap client sampai ada hasil;
    bila endpoint detail tidak terbaca, jatuh ke daftar nama+slug biasa. -> (list, catatan)"""
    root, notes = f"{proto}://{host}", []
    for client in clients:
        h = {"Authorization": f"Bearer {tok}", "client": client, "Accept": "application/json", "Content-Language": "id"}
        for ep, style in core.COMPANY_ENDPOINTS:
            out, seen = [], set()
            try:
                for page in range(1, 51):
                    r = requests.get(root + ep, headers=h, timeout=60, params=core._company_params(style, page))
                    if r.status_code >= 400: raise RuntimeError(f"HTTP {r.status_code}")
                    data = r.json()
                    items = core.rows_from(data) or (data if isinstance(data, list) else [data])
                    n = len(out)
                    for it in items:
                        row = _company_row(it, host) if isinstance(it, dict) else None
                        if row and row["slug"] not in seen: seen.add(row["slug"]); out.append(row)
                    if len(items) < 20 or len(out) == n: break
            except (RuntimeError, ValueError, requests.RequestException) as ex:
                notes.append(f"{ep}: {core._short(ex)}")
            out = [c for c in out if c["status"] != "INACTIVE"]
            if out:
                out.sort(key=lambda c: c["name"].casefold())
                return out, ""
    dbs, note = core.list_companies({"token": tok, "protocol": proto, "server": host}, me)
    rows = [{"name": n, "slug": s, "version": "", "edition": "", "expired": "", "status": ""} for n, s in dbs]
    return rows, ("" if rows else note or "; ".join(notes[:3]))


def client_list(host):
    own = CLIENTS.get(host.split(":")[0].lower())           # client khusus host dari env: dipakai apa adanya
    return [own] if own else list(dict.fromkeys([GO_CLIENT_B64, core.CLIENT_B64]))


def user_of(me):
    first, last = core._find_key(me, "first_name") or "", core._find_key(me, "last_name") or ""
    name = f"{first} {last}".strip() or core._find_key(me, "username") or ""
    return {"name": name, "email": core._find_key(me, "email") or ""}


# ------------------------------------------------------------------ halaman
@app.get("/")
def home():
    path = os.path.join(PUBLIC, "index.html")
    if os.path.exists(path):
        return send_file(path)
    return redirect("/index.html")


@app.get("/<path:name>")
def public_file(name):
    """Untuk jalan lokal (python app.py). Di Vercel, folder public/ dilayani CDN lebih dulu."""
    if name.startswith("api/") or not os.path.isfile(os.path.join(PUBLIC, name)): abort(404)
    return send_from_directory(PUBLIC, name)


# ------------------------------------------------------------------ API
@app.get("/api/config")
def config():
    return jsonify(default_server=core.SERVER, allowed=ALLOWED, kode_trx=core.KODE_TRX,
                   ket=core.KET_TAMBAHAN, cap=core.CAP_FASILITAS, fasilitas=list(core.KODE_FASILITAS),
                   faktur_cols=core.FAKTUR_COLS, detail_cols=core.DETAIL_COLS, num_cols=core.NUM_COLS)


@app.post("/api/login")
def login():
    b = body()
    email, pw = str(b.get("email") or "").strip(), str(b.get("password") or "")
    if not email or not pw: raise ApiError("Email dan password wajib diisi.")
    proto, host = server_of(b.get("server"))
    base = f"{proto}://{host}"
    tries = client_list(host)
    tok = me = used = None
    for i, client in enumerate(tries):
        try:
            tok, me = core.login(email, pw, base, client)
            used = client
            break
        except RuntimeError as ex:
            m = re.search(r"HTTP (\d+)", str(ex))
            code = int(m.group(1)) if m else 502
            print(f"[login] host={host} http={code} client_id={_client_id(client)} percobaan={i + 1}/{len(tries)}", flush=True)
            if code == 401 and i < len(tries) - 1:
                continue                                  # coba client berikutnya
            # Pesan mentah dari Zahir berupa JSON; tampilkan versi singkat yang jelas di layar login.
            if code in (400, 401):
                raise ApiError("Email atau password salah. Periksa kembali akun Zahir Anda.", 401)
            if code == 403:
                raise ApiError("Akun ini tidak diizinkan masuk ke server tersebut.", 403)
            raise ApiError(f"Login gagal (HTTP {code}). Coba lagi atau periksa alamat server.", 502)
    tries = [used] + [x for x in tries if x != used]      # client yang berhasil dicoba lebih dulu
    cos, note = companies_full(tok, proto, host, tries, me)
    return jsonify(token=tok, server=host, user=user_of(me), companies=cos, note=note)


@app.post("/api/companies")
def companies():
    b = body()
    auth = request.headers.get("Authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not tok: raise ApiError("Sesi berakhir. Silakan login ulang.", 401)
    proto, host = server_of(b.get("server"))
    cos, note = companies_full(tok, proto, host, client_list(host))
    return jsonify(companies=cos, note=note)


@app.post("/api/connect")
def connect():
    b = body(); f = conn(b)
    core.run("SELECT 1 AS ok", f)
    ok, msg, exp = core.license_state(f["slug"], b.get("license"))
    return jsonify(slug=f["slug"], license={"ok": ok, "message": msg, "expiry": exp.isoformat() if exp else None})


@app.post("/api/license")
def license_check():
    b = body()
    slug = str(b.get("slug") or "").strip().lower()
    ok, msg, exp = core.license_state(slug, b.get("code"))
    return jsonify(ok=ok, message=msg, expiry=exp.isoformat() if exp else None)


@app.post("/api/customers")
def customers():
    f = conn(body())
    seen, out = set(), []
    for r in core.run(core.Q_CUSTOMERS, f):
        n = str(r.get("nama") or "").strip()
        if n and n not in seen: seen.add(n); out.append({"id": str(r.get("id")), "name": n})
    return jsonify(customers=out)


@app.post("/api/diagnose")
def diagnose():
    return jsonify(results=[{"name": n, "ok": ok, "detail": d} for n, ok, d in core.diagnose(conn(body()))])


@app.post("/api/preview")
def preview():
    f = job(body())
    fak, det = core.fetch(f)
    num = lambda x: float(x) if isinstance(x, (int, float)) else 0.0
    return jsonify(faktur=clean(fak), detail=clean(det), warnings=core.validate(fak, det),
                   totals={"faktur": len(fak), "detail": len(det),
                           "dpp": sum(num(r.get("DPP")) for r in det), "ppn": sum(num(r.get("PPN")) for r in det)})


@app.post("/api/export/<fmt>")
def export(fmt):
    if fmt not in ("xlsx", "xml"): raise ApiError("Format tidak dikenal.", 404)
    b = body(); f = job(b)
    if not re.fullmatch(r"\d{16}", re.sub(r"\D", "", f["npwp"])): raise ApiError("Isi NPWP Penjual 16 digit.")
    fak, det = core.fetch(f)
    if not fak: raise ApiError("Tidak ada faktur pada periode ini, file tidak dibuat.")
    buf = core.build_xlsx(fak, det, f["npwp"]) if fmt == "xlsx" else core.build_xml(fak, det, f["npwp"])
    name = f"Faktur_Keluaran_Coretax_{f['d1']}_{f['d2']}.{fmt}"
    mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "application/xml")
    return send_file(buf, mimetype=mime, as_attachment=True, download_name=name)


@app.post("/api/refs/parse")
def refs_parse():
    up = request.files.get("file")
    if not up: raise ApiError("Pilih file template .xlsx.")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        up.save(tmp.name)
        got = core.parse_ref_xlsx(tmp.name)
    if not got: raise ApiError("Tabel Kode Transaksi / Keterangan Tambahan / Cap Fasilitas tidak ditemukan di file ini.")
    return jsonify(refs={k: [list(x) for x in v] for k, v in got.items()})


@app.get("/api/health")
def health():
    return jsonify(ok=True, time=dt.datetime.utcnow().isoformat() + "Z",
                   client_id=_client_id(core.CLIENT_B64), client_dari_env=CLIENT_FROM_ENV,
                   client_per_host=sorted(CLIENTS))


if __name__ == "__main__":
    app.run(debug=True, port=5000)

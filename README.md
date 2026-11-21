# Config-Driven Web Scraper (Crawl4AI + Python)

> **Atribusi:** Proyek ini memakai [Crawl4AI](https://github.com/unclecode/crawl4ai) untuk web data extraction. Baris ini wajib dicantumkan di README dan deskripsi video (syarat atribusi di README Crawl4AI).

Scraper berbasis Python yang **tidak terikat ke satu website**. URL, mode ekstraksi, dan skema data didefinisikan secara independen di file konfigurasi per target di direktori `targets/` dan skema di `schemas/`. 

Mendukung **Two-Stage Crawling** (halaman katalog ke halaman detail) dan ekstraksi data anak (**One-to-Many Sub-Items / Varian**) secara idempoten dan dinamis ke database SQLite.

Panduan konsep arsitektur lengkap dapat dibaca pada [scrape_concept.md](file:///d:/Fachrur%20Rizhky/Content/Web%20Scraper/Crawl4AI/scrape_concept.md).

---

## Persyaratan Sistem
- Python 3.10+ (direkomendasikan Python 3.12)
- Virtual Environment aktif (`.venv`)

---

## Instalasi & Aktivasi Lingkungan

### 1. Buat Virtual Environment
```bash
python -m venv .venv
```

### 2. Aktifkan Virtual Environment

- **PowerShell (Windows):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
  *(Jika muncul error execution policy: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`)*

- **Git Bash (Windows):**
  ```bash
  source .venv/Scripts/activate
  ```

- **Linux / macOS:**
  ```bash
  source .venv/bin/activate
  ```

### 3. Pasang Dependensi & Browser Playwright
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

---

## Menjalankan Scraper

Scraper dijalankan melalui modul CLI `src.runner`.

### Parameter CLI Tersedia
- `--target` atau `-t` : Nama target konfigurasi tanpa ekstensi `.yaml` (misal: `ecommerce`).
- `--db` : Lokasi file SQLite database tujuan (default: `data/scraper.db`).
- `--fixture` atau `-f` : Path file HTML katalog lokal untuk pengujian offline.
- `--detail-fixture` : Path file HTML detail lokal untuk pengujian offline.
- `--targets-dir` : Direktori kumpulan konfigurasi YAML target (default: `targets`).

---

### 1. Menjalankan Target E-Commerce (Two-Stage Live Internet)
Menjalankan crawling katalog beserta penelusuran halaman detail dan varian produk:

- **PowerShell:**
  ```powershell
  python -m src.runner --target ecommerce --db data/ecommerce.db
  ```

- **Git Bash / Linux / macOS:**
  ```bash
  python -m src.runner --target ecommerce --db data/ecommerce.db
  ```

---

### 2. Menjalankan Target Mode Offline (HTML Fixture)
Berguna untuk pengujian cepat tanpa koneksi internet:

- **PowerShell / Git Bash:**
  ```bash
  python -m src.runner --target ecommerce --fixture tests/fixtures/ecommerce_catalog_sample.html --detail-fixture tests/fixtures/ecommerce_detail_sample.html --db data/test_offline.db
  ```

---

### 3. Menjalankan Seluruh Target Sekaligus
Jika argumen `--target` ditiadakan, scraper akan menjalankan seluruh file YAML aktif yang berada di direktori `targets/`:

```bash
python -m src.runner
```

---

## Menampilkan & Memeriksa Data di Database

Database disimpan dalam format SQLite. Anda dapat memeriksa data induk maupun data relasi anak langsung melalui baris perintah:

### 1. Melihat Daftar Tabel
- **PowerShell:**
  ```powershell
  python -c "import sqlite3; conn = sqlite3.connect('data/ecommerce.db'); print('Daftar Tabel:', [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\").fetchall()])"
  ```
- **Git Bash / Linux:**
  ```bash
  python -c 'import sqlite3; conn = sqlite3.connect("data/ecommerce.db"); print("Daftar Tabel:", [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=\"table\" AND name NOT LIKE \"sqlite_%\"").fetchall()])'
  ```

---

### 2. Menampilkan Data Tabel Induk (`products`)
- **PowerShell:**
  ```powershell
  python -c "import sqlite3, json; conn = sqlite3.connect('data/ecommerce.db'); conn.row_factory = sqlite3.Row; rows = [dict(r) for r in conn.execute('SELECT id, name, price, sku FROM products LIMIT 5')]; print(json.dumps(rows, indent=2))"
  ```
- **Git Bash / Linux:**
  ```bash
  python -c 'import sqlite3, json; conn = sqlite3.connect("data/ecommerce.db"); conn.row_factory = sqlite3.Row; rows = [dict(r) for r in conn.execute("SELECT id, name, price, sku FROM products LIMIT 5")]; print(json.dumps(rows, indent=2))'
  ```

---

### 3. Menampilkan Data Relasi Anak / Varian (`product_variants`)
Melihat relasi One-to-Many antara produk induk dan varian spesifik (ukuran, warna, harga varian):

- **PowerShell:**
  ```powershell
  python -c "import sqlite3, json; conn = sqlite3.connect('data/ecommerce.db'); conn.row_factory = sqlite3.Row; rows = [dict(r) for r in conn.execute('SELECT p.id as product_id, p.name, v.size, v.color, v.price, v.stock_status FROM product_variants v JOIN products p ON v.product_id = p.id LIMIT 10')]; print(json.dumps(rows, indent=2))"
  ```
- **Git Bash / Linux:**
  ```bash
  python -c 'import sqlite3, json; conn = sqlite3.connect("data/ecommerce.db"); conn.row_factory = sqlite3.Row; rows = [dict(r) for r in conn.execute("SELECT p.id as product_id, p.name, v.size, v.color, v.price, v.stock_status FROM product_variants v JOIN products p ON v.product_id = p.id LIMIT 10")]; print(json.dumps(rows, indent=2))'
  ```

---

### 4. Menampilkan Ringkasan Jumlah Baris
- **PowerShell:**
  ```powershell
  python -c "import sqlite3; conn = sqlite3.connect('data/ecommerce.db'); print('Total Produk:', conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]); print('Total Varian:', conn.execute('SELECT COUNT(*) FROM product_variants').fetchone()[0])"
  ```
- **Git Bash / Linux:**
  ```bash
  python -c 'import sqlite3; conn = sqlite3.connect("data/ecommerce.db"); print("Total Produk:", conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]); print("Total Varian:", conn.execute("SELECT COUNT(*) FROM product_variants").fetchone()[0])'
  ```

---

## Menjalankan Tes Otomatis (Offline)

Rangkaian unit test berjalan 100% offline menggunakan HTML fixtures:

```powershell
pytest -v
```

# IndoLegalBench Server

Backend API untuk IndoLegalBench, platform penulisan dan pengukuran test case hukum untuk Veritask.

Frontend ada di repo terpisah: `IndoLegalBench-client` (Next.js).

## Arsitektur

Monolith modular. Satu unit deployment, dibagi jadi modul domain, tiap modul berlapis di dalamnya.

```
app/
  modules/              satu folder per domain
    health/             contoh pola yang sudah lengkap, baca ini dulu
    auth/               PBI-1  Login aman & manajemen akses tim
    suites/             PBI-2  Mengelola suite sebagai wadah kasus
    cases/              PBI-3  Menulis kasus hukum terstruktur
    providers/          PBI-10 Registri produk AI yang akan diukur
    runs/               Sprint 3, eksekusi pengukuran
    reports/            Sprint 4, laporan perbandingan
    audit/              Sprint 4, jejak audit
  shared/               dipakai lintas modul
    config.py           seluruh setelan aplikasi
    database.py         engine, session, Base
    exceptions.py       exception domain
    security.py         peran pengguna dan RBAC
    pagination.py       bentuk paginasi seragam
  main.py               pendaftaran router dan handler exception

migrations/             Alembic
tests/                  cermin struktur app/modules/
```

### Isi tiap modul

| File | Tugasnya | Boleh menyentuh |
|---|---|---|
| `router.py` | Terjemahkan HTTP ke pemanggilan service | service milik modulnya sendiri |
| `service.py` | Logika bisnis | repository sendiri, service modul lain |
| `repository.py` | Query database | models milik modulnya sendiri |
| `models.py` | Tabel SQLAlchemy | Base dari shared |
| `schemas.py` | Bentuk request dan response, sumber kontrak OpenAPI | Pydantic |

### Dua aturan batas modul

1. Modul boleh memanggil `service.py` modul lain. Modul **tidak boleh** mengimpor `repository.py` atau `models.py` milik modul lain.
2. `service.py` tidak boleh menyentuh HTTP. Tidak ada `Request`, `Response`, atau `HTTPException` di sana. Lempar exception dari `app/shared/exceptions.py`, biar `main.py` yang menerjemahkannya jadi HTTP.

Kalau dua aturan itu dijaga, modul kalian punya batas nyata, bukan sekadar hiasan folder.

## Menjalankan di lokal

Ada dua cara. Pilih salah satu.

### Cara cepat: seluruh stack lewat Docker

```bash
docker compose up -d --build
docker compose exec api alembic upgrade head
```

API langsung jalan di http://localhost:8000. Cocok kalau kalian cuma butuh server hidup, misalnya orang frontend yang perlu backend menyala.

Migration sengaja tidak jalan otomatis saat container start, supaya tidak ada yang mengubah skema database tanpa sadar.

### Cara pengembangan: Python di host, database di Docker

Pakai ini kalau kalian sedang mengoding backend, karena `--reload` jauh lebih enak daripada rebuild image tiap ganti baris.

#### 1. Nyalakan database

```bash
docker compose up -d db
```

Kalau tidak memakai Docker, siapkan PostgreSQL sendiri lalu sesuaikan `DATABASE_URL` di `.env`.

#### 2. Siapkan environment

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pre-commit install
cp .env.example .env
```

Lalu isi `.env` sesuai kebutuhan.

`pre-commit install` cukup sekali per clone. Setelah itu tiap commit dipindai detect-secrets, jadi kredensial tidak ikut ter-commit. CI memindai ulang, tapi lebih murah ketahuan di lokal.

#### 3. Jalankan migration

```bash
alembic upgrade head
```

#### 4. Jalankan server

```bash
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Dokumentasi interaktif: http://localhost:8000/docs
- Kontrak OpenAPI: http://localhost:8000/openapi.json
- Cek kesehatan: http://localhost:8000/health

## Perintah harian

```bash
pytest                          # jalankan seluruh test
pytest tests/modules/suites     # test satu modul saja
ruff check .                    # cek lint
ruff format .                   # rapikan format
ruff check . --fix              # perbaiki lint yang bisa diperbaiki otomatis
python scripts/export_openapi.py  # regenerate kontrak setelah mengubah schemas.py
```

Semua perintah di atas juga dijalankan CI. Jalankan di lokal sebelum push supaya PR kalian tidak merah.

## Membuat migration baru

```bash
alembic revision --autogenerate -m "tambah tabel cases"
alembic upgrade head
```

**Penting:** setiap kali ada modul baru yang punya tabel, tambahkan import model-nya di `migrations/env.py`. Kalau lupa, Alembic tidak akan melihat tabel itu dan autogenerate akan menghasilkan migration yang salah.

Selalu baca file migration hasil autogenerate sebelum di-commit. Alembic sering salah menebak, terutama untuk perubahan tipe kolom dan rename.

## Menambah modul baru

1. Buat folder di `app/modules/<nama>/`
2. Salin lima file dari modul yang sudah ada: `router.py`, `service.py`, `repository.py`, `models.py`, `schemas.py`
3. Daftarkan router-nya di `app/main.py`
4. Kalau punya tabel, tambahkan import model di `migrations/env.py`
5. Buat folder test yang mencerminkannya di `tests/modules/<nama>/`

## Kontrak OpenAPI

Kontrak dihasilkan otomatis oleh FastAPI dari schema Pydantic di tiap modul. File `schemas.py` adalah sumber kebenarannya.

Kalau kalian mengubah `schemas.py` atau menambah endpoint, artinya kontrak berubah. Yang wajib dilakukan:

1. Jalankan `python scripts/export_openapi.py` dan commit `openapi.json` di PR yang sama
2. Umumkan di grup bahwa kontrak berubah, sebutkan bagian mana
3. Orang frontend menjalankan ulang generator tipe TypeScript

CI menolak PR yang melewatkan langkah 1. Langkah 2 paling sering dilupakan, dan itu penyebab paling umum frontend tiba-tiba rusak tanpa ada yang tahu kenapa.

**Untuk orang frontend:** kontrak terbaru tersedia sebagai artefak `openapi-contract` di setiap run CI. Buka tab Actions, pilih run pada `staging`, unduh dari bagian Artifacts. Tidak perlu menjalankan server Python di mesin kalian.

## Aturan file rahasia

Jangan pernah commit `.env`, kredensial, API key, atau kunci Zitadel.

Kalau ada kredensial yang tidak sengaja ter-commit, jangan cuma menghapusnya di commit berikutnya, karena riwayat Git tetap menyimpannya. Langsung kabari tim supaya kredensialnya dicabut dan diganti.

Ini berlaku ekstra ketat untuk modul `providers`, karena yang ditangani adalah kredensial produk AI pesaing milik klien.

## Alur kerja Git

Baca `CONTRIBUTING.md`.

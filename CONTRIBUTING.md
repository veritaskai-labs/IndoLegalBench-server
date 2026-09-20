# Panduan Kontribusi IndoLegalBench

Dokumen ini berlaku untuk dua repo: `IndoLegalBench-client` (Next.js) dan `IndoLegalBench-server` (FastAPI).
Tujuannya supaya semua orang punya cara kerja yang sama dan tidak ada yang perlu bertanya tiap kali mau mulai.

Kalau ada aturan di sini yang menghambat, jangan dilanggar diam-diam. Angkat di daily atau di grup, lalu kita ubah dokumennya bersama.

---

## 1. Struktur Branch

Hanya ada dua branch permanen. Selain itu, semua branch berumur pendek dan dihapus setelah di-merge.

| Branch | Isinya | Siapa yang boleh menulis |
|---|---|---|
| `main` | Kode yang tayang di production | Tidak ada yang push langsung. Hanya merge dari `staging` |
| `staging` | Integrasi harian, di-deploy ke staging | Tidak ada yang push langsung. Hanya merge dari branch sub task lewat PR |
| `<tipe>/<pbi>-<deskripsi>` | Kerja satu orang untuk satu sub task | Pemilik branch |

**Default branch untuk PR adalah `staging`.** Jangan buka PR ke `main` kecuali memang sedang melakukan rilis.

### Kapan masuk ke mana

- Sub task selesai, lolos review, CI hijau, maka merge ke `staging`
- Satu PBI selesai seluruhnya dan lolos Definition of Done, maka `staging` di-merge ke `main`
- Saat Sprint Review, `main` di-tag dengan versi rilis

Jangan menunggu satu PBI selesai utuh baru merge ke `staging`. Sub task yang sudah selesai boleh dan harus masuk lebih dulu.

---

## 2. Penamaan Branch

Format:

```
<tipe>/<pbi>-<deskripsi-singkat>
```

Contoh:

```
feat/pbi1-zitadel-oidc
feat/pbi1-admin-members
feat/pbi2-suite-crud
feat/pbi3-case-editor
feat/pbi3-inline-validation
feat/pbi10-credential-encryption
chore/pbi1-setup-ci
chore/pbi1-db-schema-migration
docs/pbi3-case-schema-contract
test/pbi2-suite-testing
```

Tipe yang dipakai, mengikuti Conventional Commits:

| Tipe | Untuk apa |
|---|---|
| `feat` | Fitur baru yang terlihat oleh pengguna |
| `fix` | Perbaikan bug |
| `chore` | Setup, konfigurasi, dependency, migration |
| `ci` | Pipeline dan automasi |
| `docs` | Dokumentasi, ERD, kontrak |
| `test` | Penambahan atau perbaikan test |
| `refactor` | Merapikan kode tanpa mengubah perilaku |

**Aturan penting:** kalau satu sub task menyentuh dua repo, pakai nama branch yang sama persis di `client` dan `server`. Ini supaya saat Sprint Review kita bisa menelusuri satu sub task ke dua PR-nya.

Selalu sertakan kode PBI di nama branch. Tanpa itu, kita tidak bisa membuktikan PBI mana yang sudah Done.

---

## 3. Format Commit

Pakai Conventional Commits, sama seperti yang sudah berjalan di repo client.

```
<tipe>(<scope opsional>): <deskripsi singkat, huruf kecil, tanpa titik>
```

Contoh:

```
feat(case-editor): add inline validation for reference fields
fix(auth): correct redirect after Zitadel callback
chore(db): add migration for suites table
docs(api): update OpenAPI contract for cases endpoint
test(suite): add test rejecting duplicate suite names
```

Tulis deskripsi dalam Bahasa Indonesia atau Inggris, tapi konsisten dalam satu PR. Jelaskan apa yang berubah, bukan cuma "update" atau "fix bug".

---

## 4. Alur Kerja Sehari-hari

Ikuti urutan ini setiap kali mengambil sub task baru.

**1. Ambil sub task dari board**

Pastikan sub task itu belum diambil orang lain, lalu tandai atas nama kamu sebelum mulai koding.

**2. Pastikan `staging` terbaru**

```bash
git checkout staging
git pull origin staging
```

**3. Buat branch**

```bash
git checkout -b feat/pbi3-case-editor
```

**4. Kerjakan, commit kecil-kecil**

Jangan menumpuk semua pekerjaan dalam satu commit raksasa. Commit tiap kali satu bagian logis selesai.

**5. Tarik `staging` setiap pagi selama branch masih hidup**

```bash
git checkout staging
git pull origin staging
git checkout feat/pbi3-case-editor
git merge staging
```

Konflik kecil setiap hari jauh lebih murah daripada konflik besar di akhir sprint.

**6. Push dan buka Pull Request**

```bash
git push -u origin feat/pbi3-case-editor
```

Buka PR ke `staging`, isi template PR, minta satu orang review.

**7. Setelah di-approve, squash merge, lalu hapus branch**

GitHub menyediakan tombol untuk keduanya. Squash supaya riwayat `staging` bersih, satu commit per sub task.

---

## 5. Aturan yang Tidak Bisa Ditawar

Lima hal ini yang paling menentukan sprint kita selamat atau tidak.

**1. Branch hidup maksimal 2 sampai 3 hari.**
Kalau sub task kamu butuh lebih lama dari itu, sub task-nya terlalu besar. Angkat di daily supaya dipecah, jangan dipaksakan sendiri.

**2. Selalu branch dari `staging` terbaru.**
Jangan branch dari branch orang lain kecuali memang ada dependensi langsung (lihat bagian 7).

**3. Tidak ada push langsung ke `main` dan `staging`.**
Semua lewat Pull Request, tanpa kecuali, termasuk untuk perbaikan satu baris.

**4. Satu PR wajib satu approval dan CI hijau.**
Jangan merge PR sendiri tanpa direview. Jangan approve tanpa benar-benar membaca.

**5. PR yang kebesaran akan diminta dipecah.**
Kalau PR kamu mengubah lebih dari sekitar 400 baris, pertimbangkan memecahnya. Reviewer tidak bisa membaca PR raksasa dengan serius, dan review asal-asalan sama saja dengan tidak ada review.

---

## 6. Kontrak OpenAPI

Kontrak API adalah sumber kebenaran tunggal antara client dan server. Perlakukan dengan serius.

**Di mana kontraknya tinggal:** repo `IndoLegalBench-server`. FastAPI menghasilkannya otomatis dari model Pydantic, dan hasilnya di-commit ke repo supaya perubahannya terlihat di PR.

**Kalau kamu mengubah schema atau endpoint:**

1. Ubah model Pydantic di server
2. Regenerate file kontrak dan commit hasilnya di PR yang sama
3. **Umumkan di grup** bahwa kontrak berubah, sebutkan bagian mana
4. Orang frontend menjalankan ulang generator tipe TypeScript

Langkah 3 sering dilupakan dan itu penyebab paling umum frontend tiba-tiba rusak tanpa ada yang tahu kenapa.

**Kalau kamu di frontend dan butuh endpoint yang belum ada:**
Jangan menunggu. Sepakati bentuk kontraknya dulu dengan orang backend, lalu kerjakan halamanmu dengan data tiruan. Begitu endpoint aslinya jadi, tinggal disambungkan.

**Catatan khusus PBI-3:** skema kasus hukum (OpenAPI Case) dipakai untuk empat hal sekaligus, yaitu validasi di backend, validasi real-time di frontend, format ekspor ke AiYU, dan dokumentasi API. Aturan validasi ditulis sekali di schema, tidak ditulis ulang terpisah di frontend. Kalau kamu tergoda menyalin aturan validasi ke frontend secara manual, berhenti dan tanya dulu.

---

## 7. Menangani Dependensi Antar Sub Task

Beberapa sub task tidak bisa dimulai sebelum sub task lain selesai. Ini cara menanganinya.

**Sub task fondasi dikerjakan lebih dulu dan tidak masuk sistem pick up bebas:**

- `[BE] Setup repository & CI`
- `[BE] First DB schema & migration`
- `[FE] Frontend bootstrap & generate from contract`
- `[SA] Case schema contract (ERD)`

Keempatnya harus masuk `staging` secepat mungkin di awal sprint. Sebelum itu selesai, sebagian besar sub task lain akan terblokir.

**Kalau sub task kamu bergantung pada branch yang belum di-merge:**

Jangan menganggur menunggu. Branch dari branch itu, kerjakan bagianmu, lalu rebase ke `staging` setelah dependensinya masuk.

```bash
git checkout feat/pbi1-admin-members-endpoint
git checkout -b feat/pbi1-admin-members-page
# kerjakan
# setelah branch dependensinya di-merge ke staging:
git checkout staging && git pull
git checkout feat/pbi1-admin-members-page
git rebase staging
```

**Kalau kamu terblokir lebih dari setengah hari,** angkat di grup. Jangan diam menunggu, ambil sub task lain yang tidak terblokir.

---

## 8. Definition of Done

Sebuah PBI baru boleh disebut Done kalau keenam hal ini terpenuhi. Ini berlaku sama untuk PBI-1, PBI-2, PBI-3, dan PBI-10 karena ini standar tim, bukan standar per item.

- [ ] **Design Reviewed** — desain sudah ditinjau tim sebelum dikoding
- [ ] **Code Completed** — semua sub task BE, FE, dan SA selesai dan sudah di `staging`
- [ ] **Tested** — unit dan integration test jalan, coverage di atas 60 persen
- [ ] **No Blocker Bugs** — tidak ada bug yang menghentikan alur utama
- [ ] **Accepted by PO** — disetujui Product Owner
- [ ] **Live on Production** — sudah tayang, bukan hanya jalan di lokal

Sub task selesai bukan berarti PBI Done. PBI Done itu keputusan bersama di akhir, bukan klaim perorangan.

---

## 9. Rilis

Saat sebuah PBI sudah lolos seluruh Definition of Done:

1. Buka PR dari `staging` ke `main`
2. Setelah di-merge, deploy ke production
3. Saat Sprint Review, beri tag pada `main`

```bash
git checkout main
git pull origin main
git tag -a v1.0 -m "Sprint 1 Release: Fondasi dan Kontrak Output"
git push origin v1.0
```

Tentukan dari awal siapa yang bertanggung jawab atas langkah rilis ini. Jangan diputuskan di hari terakhir.

---

## 10. Setup Awal

### Client (`IndoLegalBench-client`)

```bash
git clone <url-repo-client>
cd IndoLegalBench-client
npm install
cp .env.example .env.local   # isi sesuai kebutuhan
npm run dev
```

### Server (`IndoLegalBench-server`)

```bash
git clone <url-repo-server>
cd IndoLegalBench-server
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # bukan requirements.txt, ini sudah termasuk pytest dan ruff
pre-commit install           # sekali per clone, mengaktifkan pemindaian kredensial
cp .env.example .env         # isi sesuai kebutuhan
docker compose up -d         # PostgreSQL lokal
alembic upgrade head
uvicorn app.main:app --reload
```

Dokumentasi API otomatis tersedia di `http://localhost:8000/docs` setelah server jalan.

Cara menjalankan test, membuat migration, dan meregenerate kontrak OpenAPI ada di `README.md` repo server.

### Aturan file rahasia

Jangan pernah commit `.env`, kredensial, API key, atau kunci Zitadel. Pastikan file-file itu ada di `.gitignore`.

Kalau ada kredensial yang tidak sengaja ter-commit, **jangan cuma menghapusnya di commit berikutnya**, karena riwayat Git tetap menyimpannya. Langsung kabari tim, lalu kredensialnya harus dicabut dan diganti.

Ini berlaku ekstra ketat untuk PBI-10, karena yang ditangani adalah kredensial produk AI pesaing milik klien.

---

## 11. Kalau Ragu

- Ragu soal cara kerja teknis, tanya di grup dev
- Ragu soal ruang lingkup sub task atau acceptance criteria, tanya PO
- Ragu soal aturan di dokumen ini, angkat di daily supaya dokumennya diperbaiki

Bertanya lima menit lebih murah daripada mengerjakan hal yang salah selama dua hari.

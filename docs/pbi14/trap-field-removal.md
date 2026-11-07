# Pencabutan field traps dari kontrak kasus (SCRUM-256)

Dasar: PBI-14 AC4. Klien menyatakan field jebakan tidak dipakai dan tidak
mengurangi skor (27 Oktober 2026). Penghapusan dari editor disetujui di
diskusi grup 5 November 2026. Ini bukan keputusan menghapus data historis.

## Perubahan kontrak

| Bagian | Sebelum | Sesudah |
|---|---|---|
| `POST /suites/{id}/cases`, `PUT /cases/{id}` | body punya `traps` (daftar `Trap`, divalidasi) | `traps` tidak ada di skema. Lihat "Payload lama". |
| `CaseRead` (create, read, update) | `traps` | dihapus |
| `GET /cases/{id}/completeness` | `trap_count` | dihapus |
| `ReviewCopy` (halaman review) | `traps` | dihapus |
| `GET /cases/{id}/versions/compare` | 8 bagian, termasuk `traps` | 7 bagian. `changed` tidak pernah berisi `traps`. |
| Body snapshot suite baru | `sections.traps` | tidak ada |
| Skema `Trap` | ada | dihapus |

Kelengkapan dan syarat review tidak berubah. `traps` memang tidak pernah
masuk daftar bagian wajib, sehingga `pct`, `missing`, dan `ready_for_review`
sama persis seperti sebelumnya.

## Payload lama

Klien lama yang masih mengirim `traps` di body `POST` atau `PUT` tetap
diterima (tidak ada 422 untuk isinya). Perilakunya sengaja ditentukan, bukan
kebetulan terabaikan:

- Nilai `traps` dibuang. Isinya tidak divalidasi dan tidak disimpan.
- Hanya tipe yang dicek: harus daftar atau `null`. Nilai lain (mis. string)
  tetap 422 dengan `field = "traps"`.
- Server mencatat satu peringatan `Deprecated field 'traps' was sent and
  ignored ...` dengan id kasus. Isi traps tidak pernah masuk log.
- Field disembunyikan dari OpenAPI (`SkipJsonSchema`). `scripts/gen-zod.mjs`
  di client menolak kata kunci `deprecated` dan `items` tanpa tipe, jadi
  menandainya deprecated akan membuat `npm run gen:zod` gagal.

FE baru tidak perlu mengirim `traps`. Aturan ini bisa dicabut penuh (field
ditolak atau dibiarkan sebagai field tak dikenal) setelah semua klien
berhenti mengirimnya, dengan melihat peringatan di log.

## Data historis

Tidak ada migrasi dan tidak ada penghapusan data. Kolom `cases.traps` sudah
dihapus sejak migrasi `f4b2c8e19a30`, dan `traps` hanya ada di JSON
`case_versions.content` serta `suite_snapshot_items.body`.

- Versi yang sudah tersimpan dan snapshot lama menyimpan `traps` apa adanya.
  Isinya tidak dibaca lagi oleh API dan tidak ditulis ulang.
- `POST /cases/{id}/versions` menyalin isi versi disetujui ke draf baru
  seperti biasa, termasuk `traps` lama di JSON. Penyimpanan berikutnya pada
  draf itu (`PUT`) menulis ulang isi draf itu saja tanpa `traps`. Versi
  disetujui tidak disentuh.
- Snapshot lama tetap terbaca di halaman detail sesi. Kunci `traps` di
  dalamnya diabaikan oleh respons.
- Hasil penilaian dan bukti audit tidak berubah.

Karena tidak ada perubahan penyimpanan, tidak perlu review SA dan tidak ada
langkah rollback selain `git revert` PR ini.

## Di luar cakupan

- `JudgeInputs.traps` di `runs/judge.py` milik SCRUM-224. Field itu opsional,
  tidak masuk prompt, dan tidak disentuh di sini.
- Tidak ada pengurangan skor karena jebakan.
- Tampilan FE dikerjakan di SCRUM-257. Urutan: PR server ini lebih dulu,
  lalu FE menyalin `openapi.json` dan menjalankan `npm run gen:zod`.

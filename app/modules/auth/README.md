# Auth (SCRUM-90 / SCRUM-91)

Untuk FE, QA, dan BE yang mau login. Variabel: repo root `.env.example`.

Zitadel hanya membuktikan identitas. Login looks up `users.zitadel_sub`, then `users.email` if `sub` is still empty, and stores `sub` on that first success. We don't make user baru. Cookie: `veritask_session` (UUID sesi), HttpOnly, SameSite=Lax, Path=/. Pakai host `localhost`, bukan `127.0.0.1`.

`expires_at` is set once at login to `ABSOLUTE_SESSION_LIFETIME_MINUTES` (default 720, 12 hours). That number is a proposal and still needs client confirmation. Idle (`IDLE_TIMEOUT_MINUTES`, default 30) is a separate clock. A session ends with `401 SESSION_EXPIRED` when either clock is past. A request inside both windows updates `last_activity_at` only, unless it is marked passive (SCRUM-251, below). Neither path writes `expires_at` again.

Server nyala (`AUTH_OIDC_MODE=zitadel`, port 8000):

- Login: http://localhost:8000/auth/login
- After Zitadel: http://localhost:8000/auth/done (needs `AUTH_DONE_URL_OVERRIDE` in `.env`; otherwise `{FE}/auth/done`)
- Profile: http://localhost:8000/me
- Docs: http://localhost:8000/docs

Jangan `127.0.0.1` (cookie tidak ikut). Jangan refresh URL `/auth/callback?code=...` yang lama.

Logout is **POST** with the session cookie, not a URL you open in the address bar (GET will 405 / do nothing useful). Browser sends `veritask_session` automatically on same-origin POST (`credentials: 'include'`). Example: `POST http://localhost:8000/auth/logout`.

Idle timeout (default 30 menit, `IDLE_TIMEOUT_MINUTES`): request yang lolos `get_current_user` / `require_role` me-refresh `max-age` cookie. Tanpa header passive, request itu juga memperbarui `sessions.last_activity_at`. Idle terlampaui → baris sesi dihapus, cookie dibersihkan, `401 SESSION_EXPIRED`. Request berikutnya tanpa cookie → `401 UNAUTHENTICATED`. Akun yang dinonaktifkan tetap `401 UNAUTHENTICATED` dan sesinya dihapus; cookie dibersihkan hanya untuk `SESSION_EXPIRED`. Idle **tidak** memanggil Zitadel `end_session`.

## Passive activity (SCRUM-251)

`X-Veritask-Activity: passive` is the only value that skips the `last_activity_at` write. The match is exact and case-sensitive. A missing header, a blank value, `Passive`, or any other value still refreshes activity. The header does not skip authentication, the idle check, the absolute cap, the account check, or `require_role`. The server ignores any client-supplied deadline.

A successful authenticated response sets three headers from the row and the existing settings, including on routes other than the two below. OpenAPI lists the request header and these response headers on `GET /me` and `GET /auth/done`:

- `X-Veritask-Idle-Timeout`: `IDLE_TIMEOUT_MINUTES * 60`
- `X-Veritask-Idle-Deadline`: `last_activity_at` plus that window, after this request's refresh decision
- `X-Veritask-Absolute-Expires-At`: `sessions.expires_at`

`GET /me` JSON is unchanged. This server change does not send the header from the client. Until SCRUM-254 does, the unread poll and the dashboard poll still refresh `last_activity_at`. A page left open still stays on screen after idle until a later request or a refresh. SCRUM-254 owns the warning and the logout without another business call. SCRUM-252 owns clearing the screen on a later 401.

SCRUM-254 should send `passive` on:

- `GET /notifications/unread-count` from the badge (mount, the 30-second interval, and visibility)
- the dashboard interval and its visibility refetch
- session-validation `GET /me`

These stay foreground and must not send the header: the first `GET /dashboard` when the user opens the page, dashboard Reload, opening the notification list, mark-read, and other user page-loads and mutations. The exception applies to contributor, admin, viewer, and developer_ai. Their permissions and landing pages are unchanged.

`require_role(*roles)` (alias `require_roles`): tanpa sesi `401 UNAUTHENTICATED`; peran salah `403 FORBIDDEN`. SCRUM-91 hanya mengirimkan dependency ini. Modul lain memasangnya di router mereka. Jangan `dependency_overrides[get_current_user]` di test — pakai `complete_login` di `tests/login.py` + seed `zitadel_sub` (`AUTHOR_SUB`, `REVIEWER_SUB`, `ADMIN_SUB`, `VIEWER_SUB`).

**PBI-1 AC2 (sebagian).** SCRUM-91 memenuhi AC2 untuk penjaga sesi/peran (`UNAUTHENTICATED` / `FORBIDDEN` / idle). AC2 tingkat story — setiap halaman dan aksi hanya untuk peran yang berwenang — **belum tuntas** sampai PBI-2 (`/suites`), SCRUM-92 (`/admin/users`), PBI-3 (`/cases`), dan PBI-10 (`/admin/providers`) memasang `require_role` sesuai matriks PBI-1-SA-1. `/suites*` saat ini masih bisa dipanggil tanpa sesi. Itu disengaja: bukan cakupan subtask ini.

Matriks Sprint 1 (spec untuk ticket konsumen). `/admin/providers*` dipasang di SCRUM-115; sisa baris mengikuti ticket masing-masing:

| Route | Allow | Ticket |
|---|---|---|
| `GET /suites`, `GET /suites/{id}` | author, reviewer, admin | PBI-2 |
| `POST /suites`, `PATCH /suites/{id}`, `DELETE /suites/{id}`, `POST /suites/{id}/archive` | author, admin | PBI-2 |
| `/admin/users*` | admin | SCRUM-92 |
| `/cases*` write | author, admin | PBI-3 |
| `/admin/providers*` | admin | PBI-10 / SCRUM-115 |

SCRUM-115 menulis path `/admin/ai-products`. Stub modul dan baris matriks lama menulis `/providers`. Yang dipasang adalah `/admin/providers`: prefix `/admin` sama seperti `/admin/users`, dan `providers` tetap nama resource modul ini.

Health, `/auth/login`, `/auth/callback` tidak butuh sesi. `/me` butuh sesi, semua peran.

| Method | Path | Sukses | Error |
|---|---|---|---|
| GET | `/auth/login` | 302 ke IdP (PKCE, state, nonce) | — |
| GET | `/auth/callback?code&state` | 302 ke `{FE}/auth/done` + cookie (lokal: `/auth/done` di API) | 403 `USER_NOT_REGISTERED`, 403 `USER_DEACTIVATED`, 400 `INVALID_OIDC_STATE` / `OIDC_EXCHANGE_FAILED` |
| POST | `/auth/logout` | 302 ke IdP `end_session` (cookie required) | GET in the address bar will not log you out |
| GET | `/me` | 200 `{id, name, email, role}` | 401 `UNAUTHENTICATED`, 401 `SESSION_EXPIRED` |

Error body: `{ "code": "USER_NOT_REGISTERED", "message": "..." }`. Kode 401/403 yang dipakai FE: `UNAUTHENTICATED`, `SESSION_EXPIRED`, `FORBIDDEN` (huruf besar; bukan `forbidden` / `unauthorized`). pytest memakai `AUTH_OIDC_MODE=fake`. `APP_ENV=staging` or `production` refuses fake (boot fails; no `/_fake/oidc`). Staging/prod must use `zitadel`.

Refer to https://kelompok4pplxpropensi.atlassian.net/browse/SCRUM-91 for RBAC/idle updates.

# Auth (SCRUM-90)

Untuk FE, QA, dan BE yang mau login. Variabel: repo root `.env.example`.

Zitadel hanya membuktikan identitas. Login looks up `users.zitadel_sub`, then `users.email` if `sub` is still empty, and stores `sub` on that first success. We don't make user baru. Cookie: `veritask_session` (UUID sesi), HttpOnly, SameSite=Lax, Path=/. Pakai host `localhost`, bukan `127.0.0.1`.

`expires_at` is set once at login to `ABSOLUTE_SESSION_LIFETIME_MINUTES` (default 720, 12 hours). That number is a proposal and still needs client confirmation. Idle (`IDLE_TIMEOUT_MINUTES`, default 30) is a separate clock and is enforced in SCRUM-91.

Server nyala (`AUTH_OIDC_MODE=zitadel`, port 8000):

- Login: http://localhost:8000/auth/login
- After Zitadel: http://localhost:8000/auth/done (needs `AUTH_DONE_URL_OVERRIDE` in `.env`; otherwise `{FE}/auth/done`)
- Profile: http://localhost:8000/me
- Docs: http://localhost:8000/docs 

Jangan `127.0.0.1` (cookie tidak ikut). Jangan refresh URL `/auth/callback?code=...` yang lama.

Logout is **POST** with the session cookie, not a URL you open in the address bar (GET will 405 / do nothing useful). Browser sends `veritask_session` automatically on same-origin POST (`credentials: 'include'`). Example: `POST http://localhost:8000/auth/logout`.

| Method | Path | Sukses | Error |
|---|---|---|---|
| GET | `/auth/login` | 302 ke IdP (PKCE, state, nonce) | — |
| GET | `/auth/callback?code&state` | 302 ke `{FE}/auth/done` + cookie (lokal: `/auth/done` di API) | 403 `USER_NOT_REGISTERED`, 403 `USER_DEACTIVATED`, 400 `INVALID_OIDC_STATE` / `OIDC_EXCHANGE_FAILED` |
| POST | `/auth/logout` | 302 ke IdP `end_session` (cookie required) | GET in the address bar will not log you out |
| GET | `/me` | 200 `{id, name, email, role}` | 401 `UNAUTHENTICATED` |

Error body: `{ "code": "USER_NOT_REGISTERED", "message": "..." }`. pytest memakai `AUTH_OIDC_MODE=fake`. `APP_ENV=staging` or `production` refuses fake (boot fails; no `/_fake/oidc`). Staging/prod must use `zitadel`.

Refer to https://kelompok4pplxpropensi.atlassian.net/browse/SCRUM-90 for updates.
 
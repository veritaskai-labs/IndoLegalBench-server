# Deploying IndoLegalBench

How to run both tiers on a server, written for the UAT deployment on Veritask's
GCP Kubernetes cluster. It covers what the two images need; how to express that
in manifests is up to whoever runs the cluster.

| | API (this repo) | Web app ([IndoLegalBench-client](https://github.com/veritaskai-labs/IndoLegalBench-client)) |
|---|---|---|
| Domain | `https://api.legalbench.veritask.ai` | `https://legalbench.veritask.ai` |
| Image | `Dockerfile` in this repo | `Dockerfile` in the client repo |
| Port | 8000 | 3000 |
| Runs as | `appuser` (uid 1000) | `node` |
| Health path | `GET /health` | `GET /login` |
| Replicas | **exactly 1**, see below | any |

Plus a PostgreSQL 16 database and Veritask's Zitadel at `https://auth8.veritask.ai`.

## Read this first

1. **Run the API as a single replica.** The state of an unfinished login
   (between `/auth/login` and `/auth/callback`) is kept in the API process's
   memory (`app/modules/auth/pending.py`). With two pods, a callback that lands
   on the other pod fails with `INVALID_OIDC_STATE`. Sessions themselves live
   in the database, so this only affects the login step. Moving the pending
   state into the database is a follow-up.
2. **Keep both domains under `veritask.ai`.** The session cookie is set by the
   API with `SameSite=Lax` and no `Domain`, so the browser only sends it on the
   web app's API calls because both hosts are the same site. Serving the API
   from a different registrable domain breaks every login silently: `/me`
   answers 401 right after a successful login.
3. **Serve both over HTTPS.** `COOKIE_SECURE=true` makes the browser drop the
   cookie on plain HTTP.

## 1. Build the images

API, from the root of this repo:

```bash
docker build -t indolegalbench-server .
```

Web app, from the root of the client repo. The API address is compiled into the
JavaScript, so it is a build argument, not a runtime env var:

```bash
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.legalbench.veritask.ai -t indolegalbench-client .
```

CI builds and smoke tests both images but does not push them to a registry yet.

## 2. Database

PostgreSQL 16 (what `docker-compose.yml` and CI use). Cloud SQL or in-cluster
both work. Create an empty database and a user that owns it. The migrations
create everything else.

## 3. API environment variables

The web app needs no runtime variables beyond its build argument.

| Variable | Value for UAT | Notes |
|---|---|---|
| `APP_ENV` | `staging` | Also makes the API refuse to start with fake login |
| `DEBUG` | `false` | |
| `DATABASE_URL` | `postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME` | **Secret.** The `+psycopg` driver prefix is required, a plain `postgres://` URL fails. URL-encode special characters in the password <!-- pragma: allowlist secret --> |
| `CORS_ORIGINS` | `["https://legalbench.veritask.ai"]` | JSON list. The first entry is also where users land after login and logout |
| `AUTH_OIDC_MODE` | `zitadel` | |
| `PUBLIC_BASE_URL` | `https://api.legalbench.veritask.ai` | The Zitadel redirect URI is derived from it |
| `ZITADEL_ISSUER` | `https://auth8.veritask.ai` | No trailing slash |
| `ZITADEL_CLIENT_ID` | client ID of the `IndoLegalBenchFrontend` app | Not a secret for a public PKCE client, but it is kept out of this public repo |
| `ZITADEL_CLIENT_SECRET` | empty | The app is a public client with PKCE |
| `ZITADEL_AUDIENCE` | empty | Defaults to the client ID |
| `COOKIE_SECURE` | `true` | Requires HTTPS, see "Read this first" |
| `AUTH_DONE_URL_OVERRIDE` | empty | Local testing only. If set, logins never return to the web app |
| `IDLE_TIMEOUT_MINUTES` | `30` | |
| `ABSOLUTE_SESSION_LIFETIME_MINUTES` | `720` | 12 hours |

Leave everything else unset. `CREDENTIAL_ENCRYPTION_KEY` is only used from
PBI-10 onward.

## 4. Migrations

The container does not migrate on start. Before each rollout, run this once
with the same image and the same environment (a Kubernetes Job or an
initContainer both work):

```bash
alembic upgrade head
```

It is safe to run repeatedly. On a fresh database it creates the users,
sessions and suites tables.

## 5. First admin account

Nobody can log in until an admin exists in the database, and fake login is
disabled under `APP_ENV=staging`. Insert the first admin once, after the
migrations:

```sql
INSERT INTO users (id, email, name, role, is_active)
VALUES (gen_random_uuid(), 'staff10@veritask.ai', 'Staff 10', 'admin', true)
ON CONFLICT (email) DO NOTHING;
```

Emails are stored lowercase. The Zitadel account is linked on its first login,
and the name is refreshed from Zitadel then. Everyone else is added by that
admin from the Anggota (Members) page.

## 6. Probes

- API: `GET /health` on port 8000. It answers 200 whenever the process is up,
  and reports `"database": "ok"` or `"unreachable"` in the body, so a database
  outage does not restart the pod.
- Web app: `GET /login` on port 3000. It answers 200 without a session.

## 7. Zitadel (Veritask)

Add these to the `IndoLegalBenchFrontend` application, next to the existing
localhost ones:

- Redirect URI: `https://api.legalbench.veritask.ai/auth/callback`
- Post-logout URI: `https://legalbench.veritask.ai/login`

They must match exactly, including `https://` and without a trailing slash.

## 8. Check the deployment

1. `https://api.legalbench.veritask.ai/health` shows `"database":"ok"`.
2. Open `https://legalbench.veritask.ai/login` in a private window and click
   **Masuk dengan akun Veritask**.
3. Log in as staff10. You land on `/admin/members` as Admin.
4. Click **Keluar**. You end up back on `/login`.

| Symptom | Likely cause |
|---|---|
| Zitadel shows a redirect URI error | Section 7 not done, or `PUBLIC_BASE_URL` differs from the registered URI |
| Login works, then you are sent straight back to `/login` | Cookie not sent: domains not under `veritask.ai`, or not HTTPS |
| `INVALID_OIDC_STATE` after logging in | More than one API replica |
| "Akun Anda belum terdaftar" | Section 5 not done, or the email differs from the Zitadel account |
| Login ends on `api.legalbench.veritask.ai/auth/done` | `AUTH_DONE_URL_OVERRIDE` is set |

## Not covered yet

- No image registry push from CI.
- `/docs` and `/openapi.json` are public. Fine for UAT with test data, worth
  closing before production.
- Sessions that simply expire in the browser are never deleted from the
  database. Harmless for UAT, needs a periodic cleanup later.

# Deploying IndoLegalBench

How to run both tiers for the UAT, on the dedicated Tencent Cloud VM that
Veritask provides (2 vCPU, 8 GB RAM, 70 GB, Ubuntu, hosted in Indonesia). We
run everything on it ourselves with Docker Compose. The files are in
[`deploy/`](../deploy):

| File | What it is |
|---|---|
| `compose.yml` | Caddy, API, web app and PostgreSQL 16 |
| `Caddyfile` | HTTPS for both domains, certificates are automatic |
| `.env.example` | The few values the stack needs. Copy to `.env` on the VM |
| `deploy.sh` | Pull both repos, rebuild, migrate, restart |
| `backup.sh` | Daily database dump, keeps 7 days |

| | API (this repo) | Web app ([IndoLegalBench-client](https://github.com/veritaskai-labs/IndoLegalBench-client)) |
|---|---|---|
| Domain | `https://api.indolegalbench.veritask.ai` | `https://indolegalbench.veritask.ai` |
| Image | `Dockerfile` in this repo | `Dockerfile` in the client repo |
| Port inside the network | 8000 | 3000 |
| Runs as | `appuser` (uid 1000) | `node` |
| Health path | `GET /health` | `GET /login` |

Login goes through Veritask's Zitadel at `https://auth8.veritask.ai`.

## Read this first

1. **Run exactly one API container.** The state of an unfinished login
   (between `/auth/login` and `/auth/callback`) is kept in the API process's
   memory (`app/modules/auth/pending.py`). With two, a callback that lands on
   the other one fails with `INVALID_OIDC_STATE`. Sessions themselves live in
   the database, so this only affects the login step. Do not
   `docker compose up --scale api=2` or add uvicorn workers. Moving the pending
   state into the database is a follow-up.
2. **Keep both domains under `veritask.ai`.** The session cookie is set by the
   API with `SameSite=Lax` and no `Domain`, so the browser only sends it on the
   web app's API calls because both hosts are the same site. Serving the API
   from a different registrable domain breaks every login silently: `/me`
   answers 401 right after a successful login.
3. **Serve both over HTTPS.** `COOKIE_SECURE=true` makes the browser drop the
   cookie on plain HTTP. Caddy handles this, as long as step 1 below is done.

## 1. Before you start (Veritask)

- Both domains have an A record pointing at the VM's public IP.
- The security group allows inbound 22, 80 and 443 (TCP), and 443 UDP if you
  want HTTP/3. Caddy needs 80 and 443 reachable from the internet to get
  certificates.
- Our SSH public keys are on the VM.
- Zitadel: the `IndoLegalBenchFrontend` application has these, next to the
  existing localhost ones. They must match exactly, including `https://` and
  without a trailing slash:
  - Redirect URI: `https://api.indolegalbench.veritask.ai/auth/callback`
  - Post-logout URI: `https://indolegalbench.veritask.ai/login`

Check DNS from your own machine before going further:

```bash
nslookup indolegalbench.veritask.ai
nslookup api.indolegalbench.veritask.ai
```

Both must answer the VM's IP. If Caddy starts before that, it fails to get
certificates and retries with a growing delay.

## 2. Prepare the VM (once)

```bash
ssh ubuntu@VM_IP        # user from Veritask; IP is what both domains resolve to

# Docker Engine and the compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
exit   # log in again so the group applies

# Both repos side by side. They are public, no credentials needed.
sudo mkdir -p /opt/indolegalbench && sudo chown "$USER" /opt/indolegalbench
cd /opt/indolegalbench
git clone https://github.com/veritaskai-labs/IndoLegalBench-server.git
git clone https://github.com/veritaskai-labs/IndoLegalBench-client.git
```

## 3. Configure

```bash
cd /opt/indolegalbench/IndoLegalBench-server/deploy
cp .env.example .env
chmod 600 .env
openssl rand -hex 24                     # paste as POSTGRES_PASSWORD
openssl rand -base64 32 | tr '+/' '-_'   # paste as CREDENTIAL_ENCRYPTION_KEY
nano .env                                # also fill ZITADEL_CLIENT_ID
```

`CREDENTIAL_ENCRYPTION_KEY` encrypts the AI product credentials (PBI-10).
Store a copy somewhere other than the VM. The database dumps do not contain
it, and a changed or lost key makes every stored credential unreadable: the
admin would have to enter each one again. `deploy.sh` stops if it is empty.
On an install that predates PBI-10, add it to `.env` before the next deploy.

`.env` holds only what differs per install. The rest of the API settings are
fixed in `compose.yml` (full list in section 7). The web app needs no runtime
variables: the API address is compiled into its JavaScript from `API_DOMAIN`
at build time, so changing the domain means rebuilding.

## 4. Deploy

```bash
./deploy.sh
```

It pulls `staging` in both repos, builds both images, starts the database, runs
`alembic upgrade head`, then starts everything. The first build takes a few
minutes, most of it the web app. Run the same command for every later update.
Set `DEPLOY_BRANCH=main` to deploy another branch.

Migrations run once per deploy, not on container start. They are safe to run
repeatedly. On a fresh database they create the users, sessions and suites
tables.

## 5. First admin account

Nobody can log in until an admin exists in the database, and fake login is
disabled under `APP_ENV=staging`. Insert the first admin once, after the first
deploy:

```bash
docker compose exec db psql -U indolegalbench -c "
INSERT INTO users (id, email, name, role, is_active)
VALUES (gen_random_uuid(), 'staff10@veritask.ai', 'Staff 10', 'admin', true)
ON CONFLICT (email) DO NOTHING;"
```

Emails are stored lowercase. The Zitadel account is linked on its first login,
and the name is refreshed from Zitadel then. Everyone else is added by that
admin from the Anggota (Members) page.

## 6. Check the deployment

1. `https://api.indolegalbench.veritask.ai/health` shows `"database":"ok"`.
2. Open `https://indolegalbench.veritask.ai/login` in a private window and click
   **Masuk dengan akun Veritask**.
3. Log in as staff10. You land on `/admin/members` as Admin.
4. Click **Keluar**. You end up back on `/login`.

| Symptom | Likely cause |
|---|---|
| Browser shows a certificate error, or Caddy logs ACME errors | DNS not pointing at the VM yet, or port 80/443 closed. `docker compose logs caddy` |
| Zitadel shows a redirect URI error | Zitadel URIs from section 1 missing, or `API_DOMAIN` differs from the registered URI |
| Login works, then you are sent straight back to `/login` | Cookie not sent: domains not under `veritask.ai`, or not HTTPS |
| `INVALID_OIDC_STATE` after logging in | More than one API process, or the API restarted mid-login (just retry) |
| "Akun Anda belum terdaftar" | Section 5 not done, or the email differs from the Zitadel account |
| Web app loads but every request fails | Built with the wrong `API_DOMAIN`. Fix `.env`, run `./deploy.sh` again |

Useful commands, from `deploy/`:

```bash
docker compose ps
docker compose logs -f api        # or web, caddy, db
docker compose restart api
```

## 7. API settings

For reference. `compose.yml` already sets these, only the ones marked `.env`
come from the env file.

| Variable | Value for UAT | Notes |
|---|---|---|
| `APP_ENV` | `staging` | Also makes the API refuse to start with fake login |
| `DEBUG` | `false` | |
| `DATABASE_URL` | built from `POSTGRES_PASSWORD` (`.env`) | The `+psycopg` driver prefix is required |
| `CORS_ORIGINS` | `["https://" + WEB_DOMAIN]` (`.env`) | JSON list. The first entry is also where users land after login and logout |
| `AUTH_OIDC_MODE` | `zitadel` | |
| `PUBLIC_BASE_URL` | `"https://" + API_DOMAIN` (`.env`) | The Zitadel redirect URI is derived from it |
| `ZITADEL_ISSUER` | `https://auth8.veritask.ai` (`.env`) | No trailing slash |
| `ZITADEL_CLIENT_ID` | client ID of `IndoLegalBenchFrontend` (`.env`) | Not a secret for a public PKCE client, but kept out of this public repo |
| `COOKIE_SECURE` | `true` | Requires HTTPS |
| `IDLE_TIMEOUT_MINUTES` | `30` | |
| `ABSOLUTE_SESSION_LIFETIME_MINUTES` | `720` | 12 hours |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key (`.env`) | See section 3. Never change it on a running install |

Left unset on purpose: `ZITADEL_CLIENT_SECRET` and `ZITADEL_AUDIENCE` (public
client with PKCE, audience defaults to the client ID) and `AUTH_DONE_URL_OVERRIDE`
(local testing only; if set, logins never return to the web app).

## 8. Backups

`backup.sh` dumps the database to `/var/backups/indolegalbench/` and keeps 7
days. Schedule it once:

```bash
sudo mkdir -p /var/backups/indolegalbench && sudo chown "$USER" /var/backups/indolegalbench
crontab -e
# add:
0 2 * * * /opt/indolegalbench/IndoLegalBench-server/deploy/backup.sh >> /var/backups/indolegalbench/backup.log 2>&1
```

The dumps stay on the VM, so they cover "we broke the data", not "the VM is
gone". They also do not include `CREDENTIAL_ENCRYPTION_KEY`; a restore on a new
VM needs the copy of the key kept in section 3. A VM snapshot on Veritask's side, or copying the dumps to a bucket,
covers the second case.

Restoring a dump replaces the current data:

```bash
gunzip -c /var/backups/indolegalbench/indolegalbench-YYYY-MM-DD.sql.gz \
  | docker compose exec -T db psql -U indolegalbench -d indolegalbench
```

## 9. Audit log retention

Audit log rows are kept 90 days, then deleted permanently (PBI-18 AC7,
client decision). Nobody else can delete them: a database trigger rejects
every UPDATE, DELETE and TRUNCATE on `audit_logs`, the table owner included.
The one exception is the role `ilb_retention`, and only for rows older than
90 days. Create that role once, then let cron run the job daily:

```bash
openssl rand -hex 24   # paste below and into .env as part of AUDIT_RETENTION_DATABASE_URL
docker compose exec -T db psql -U indolegalbench -d indolegalbench \
  -c "CREATE ROLE ilb_retention LOGIN PASSWORD '<password>'" \
  -c "GRANT SELECT, DELETE ON audit_logs TO ilb_retention"
# .env:
# AUDIT_RETENTION_DATABASE_URL=postgresql+psycopg://ilb_retention:<password>@db:5432/indolegalbench
crontab -e
# add:
30 2 * * * cd /opt/indolegalbench/IndoLegalBench-server/deploy && docker compose run --rm api python -m app.modules.audit.retention_job >> /var/backups/indolegalbench/audit-retention.log 2>&1
```

Each run logs how many rows it deleted. With `AUDIT_RETENTION_DATABASE_URL`
empty the job exits with an error and deletes nothing.

## Not covered yet

- **No CI/CD yet.** Deploys are `./deploy.sh` over SSH. A GitHub Actions job
  that SSHes in and runs it on merge to `staging` is the natural next step.
  It needs a dedicated deploy key stored as a repo secret, never a personal
  key.
- `/docs` and `/openapi.json` are public. Fine for UAT with test data, worth
  closing before production.
- Sessions that simply expire in the browser are never deleted from the
  database. Harmless for UAT, needs a periodic cleanup later.
- Monitoring. Veritask's own Tencent-based infra is the plan for that.

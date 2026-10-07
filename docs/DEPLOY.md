# Deploying IndoLegalBench

How to run IndoLegalBench on the dedicated Tencent Cloud VM that Veritask
provides (2 vCPU, 8 GB RAM, 70 GB, Ubuntu, hosted in Indonesia). We run
everything on it ourselves with Docker Compose. The VM holds two independent
stacks behind one Caddy:

| Stack | Web | API | Deploys | Used for |
|---|---|---|---|---|
| `dev` | `https://dev.indolegalbench.veritask.ai` | `https://devapi.indolegalbench.veritask.ai` | `staging`, automatically on every merge | UAT, demos, trying things |
| `prod` | `https://indolegalbench.veritask.ai` | `https://api.indolegalbench.veritask.ai` | `main`, by hand, on release only | Veritask's real work |

Each stack has its own checkout of both repos, its own `.env`, database,
encryption key and backups. Nothing is shared between them except the VM and
Caddy.

```
/opt/indolegalbench/           prod checkouts (also runs deploy/proxy)
/opt/indolegalbench-dev/       dev checkouts

            :80 :443
               |
   deploy/proxy  (compose project ilb-proxy, one Caddy)
               |  network ilb-edge
       +-------+--------+
   prod-web  prod-api   dev-web  dev-api
   (project deploy)     (project ilb-dev)
       |                    |
    prod db              dev db
```

The files are in [`deploy/`](../deploy):

| File | What it is |
|---|---|
| `proxy/compose.yml` | The one Caddy, owns ports 80 and 443 |
| `proxy/Caddyfile` | HTTPS for all four domains, certificates are automatic |
| `proxy/.env.example` | The four domains. Copy to `proxy/.env` on the VM |
| `compose.yml` | One app stack: API, web app and PostgreSQL 16 |
| `.env.example` | The values one stack needs. Copy to `.env` on the VM, once per stack |
| `deploy.sh` | Pull both repos, rebuild, migrate, restart one stack |
| `backup.sh` | Daily database dump of one stack, keeps 7 days |

| | API (this repo) | Web app ([IndoLegalBench-client](https://github.com/veritaskai-labs/IndoLegalBench-client)) |
|---|---|---|
| Image | `Dockerfile` in this repo, tagged `indolegalbench-server:<stack>` | `Dockerfile` in the client repo, tagged `indolegalbench-client:<stack>` |
| Name on `ilb-edge` | `<stack>-api`, port 8000 | `<stack>-web`, port 3000 |
| Runs as | `appuser` (uid 1000) | `node` |
| Health path | `GET /health` | `GET /login` |

Login goes through Veritask's Zitadel at `https://auth8.veritask.ai`.

## Read this first

1. **Run exactly one API container per stack.** The state of an unfinished
   login (between `/auth/login` and `/auth/callback`) is kept in the API
   process's memory (`app/modules/auth/pending.py`). With two, a callback that
   lands on the other one fails with `INVALID_OIDC_STATE`. Sessions themselves
   live in the database, so this only affects the login step. Do not
   `docker compose up --scale api=2` or add uvicorn workers. Moving the pending
   state into the database is a follow-up.
2. **Keep every domain under `veritask.ai`.** The session cookie is set by the
   API with `SameSite=Lax` and no `Domain`, so the browser only sends it on the
   web app's API calls because both hosts are the same site. Serving the API
   from a different registrable domain breaks every login silently: `/me`
   answers 401 right after a successful login. **Never add a `Domain` to the
   cookie**: host-only cookies are what keep a dev session from leaking into
   prod and the other way round.
3. **Serve everything over HTTPS.** `COOKIE_SECURE=true` makes the browser drop
   the cookie on plain HTTP. Caddy handles this, as long as section 1 is done.
4. **`COMPOSE_PROJECT_NAME` must differ per stack.** Both checkouts call the
   folder `deploy`, which is also compose's default project name. Without an
   explicit name, `dev` would replace `prod`'s containers. `deploy.sh` and
   `backup.sh` refuse to run without it.

## 1. Before you start (Veritask)

- All four domains have an A record pointing at the VM's public IP.
- The security group allows inbound 22, 80 and 443 (TCP), and 443 UDP if you
  want HTTP/3. Caddy needs 80 and 443 reachable from the internet to get
  certificates.
- Our SSH public keys are on the VM.
- Zitadel: the IndoLegalBench application has these, next to the existing
  localhost ones. They must match exactly, including `https://` and without a
  trailing slash:

  | | Redirect URI | Post-logout URI |
  |---|---|---|
  | prod | `https://api.indolegalbench.veritask.ai/auth/callback` | `https://indolegalbench.veritask.ai/login` |
  | dev | `https://devapi.indolegalbench.veritask.ai/auth/callback` | `https://dev.indolegalbench.veritask.ai/login` |

  If dev uses a separate Zitadel application, its client ID goes into the dev
  `.env` only.

Check DNS from your own machine before going further:

```bash
nslookup indolegalbench.veritask.ai
nslookup api.indolegalbench.veritask.ai
nslookup dev.indolegalbench.veritask.ai
nslookup devapi.indolegalbench.veritask.ai
```

All four must answer the VM's IP. If Caddy starts before that, it fails to get
certificates and retries with a growing delay.

## 2. Prepare the VM (once)

```bash
ssh ubuntu@VM_IP        # user from Veritask; IP is what the domains resolve to

# Docker Engine and the compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
exit   # log in again so the group applies

# One pair of checkouts per stack. The repos are public, no credentials needed.
for dir in /opt/indolegalbench /opt/indolegalbench-dev; do
  sudo mkdir -p "$dir" && sudo chown "$USER" "$dir"
  git -C "$dir" clone https://github.com/veritaskai-labs/IndoLegalBench-server.git
  git -C "$dir" clone https://github.com/veritaskai-labs/IndoLegalBench-client.git
done
```

## 3. Configure

The proxy, from the prod checkout:

```bash
cd /opt/indolegalbench/IndoLegalBench-server/deploy/proxy
cp .env.example .env     # the four domains, normally unchanged
```

Then each stack. For dev, in `/opt/indolegalbench-dev/IndoLegalBench-server/deploy`,
and for prod, in `/opt/indolegalbench/IndoLegalBench-server/deploy`:

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 24                     # paste as POSTGRES_PASSWORD
openssl rand -base64 32 | tr '+/' '-_'   # paste as CREDENTIAL_ENCRYPTION_KEY
nano .env                                # also fill ZITADEL_CLIENT_ID
```

`.env.example` is filled in for dev. For prod change these, as its comments
say:

| Variable | dev | prod |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `ilb-dev` | `deploy` |
| `STACK` | `dev` | `prod` |
| `APP_ENV` | `staging` | `production` |
| `DEPLOY_BRANCH` | `staging` | `main` |
| `WEB_DOMAIN` | `dev.indolegalbench.veritask.ai` | `indolegalbench.veritask.ai` |
| `API_DOMAIN` | `devapi.indolegalbench.veritask.ai` | `api.indolegalbench.veritask.ai` |
| `BACKUP_DIR` | `/var/backups/indolegalbench-dev` | `/var/backups/indolegalbench` |

prod is called `deploy` because that was the project name before the split,
and keeping it keeps its database volume (`deploy_postgres_data`).

`CREDENTIAL_ENCRYPTION_KEY` encrypts the AI product credentials (PBI-10). Use a
different key per stack, and store a copy of each somewhere other than the VM.
The database dumps do not contain it, and a changed or lost key makes every
stored credential unreadable: the admin would have to enter each one again.
`deploy.sh` stops if it is empty.

`.env` holds only what differs per install. The rest of the API settings are
fixed in `compose.yml` (full list in section 7). The web app needs no runtime
variables: the API address is compiled into its JavaScript from `API_DOMAIN`
at build time, so changing the domain means rebuilding.

## 4. Deploy

Start the proxy once. It creates the `ilb-edge` network the stacks join:

```bash
cd /opt/indolegalbench/IndoLegalBench-server/deploy/proxy
docker compose up -d
```

Then each stack, from its own `deploy/` folder:

```bash
./deploy.sh
```

It pulls `DEPLOY_BRANCH` in both repos of that checkout, builds both images,
starts the database, runs `alembic upgrade head`, then starts everything. The
first build takes a few minutes, most of it the web app. Run the same command
for every later update. Only one deploy runs at a time on the VM; a second one
waits for the first.

dev is redeployed automatically after every merge to `staging` (section 9).
prod is deployed by hand after a release (section 10).

Migrations run once per deploy, not on container start. They are safe to run
repeatedly.

After changing `proxy/Caddyfile`, pull the prod checkout and reload Caddy
without downtime:

```bash
cd /opt/indolegalbench/IndoLegalBench-server/deploy/proxy
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## 5. First admin account

Nobody can log in until an admin exists in the database, and fake login is
disabled in both stacks. Insert the first admin once per stack, after its
first deploy, from its `deploy/` folder:

```bash
docker compose exec db psql -U indolegalbench -c "
INSERT INTO users (id, email, name, role, is_active)
VALUES (gen_random_uuid(), 'staff10@veritask.ai', 'Staff 10', 'admin', true)
ON CONFLICT (email) DO NOTHING;"
```

Emails are stored lowercase. The Zitadel account is linked on its first login,
and the name is refreshed from Zitadel then. Everyone else is added by that
admin from the Anggota (Members) page. The two stacks have separate member
lists.

## 6. Check the deployment

For each stack (the dev domains shown):

1. `https://devapi.indolegalbench.veritask.ai/health` shows `"database":"ok"`
   and the stack's `APP_ENV`.
2. Open `https://dev.indolegalbench.veritask.ai/login` in a private window and
   click **Masuk dengan akun Veritask**.
3. Log in as staff10. You land on `/admin/members` as Admin.
4. Click **Keluar**. You end up back on `/login`.

| Symptom | Likely cause |
|---|---|
| Browser shows a certificate error, or Caddy logs ACME errors | DNS not pointing at the VM yet, or port 80/443 closed. `docker compose logs caddy` in `deploy/proxy` |
| `502` on one stack's domains | That stack is not running or not on `ilb-edge`. `docker compose ps` in its `deploy/`, then `./deploy.sh` |
| Zitadel shows a redirect URI error | Zitadel URIs from section 1 missing, or `API_DOMAIN` differs from the registered URI |
| Login works, then you are sent straight back to `/login` | Cookie not sent: domains not under `veritask.ai`, or not HTTPS |
| `INVALID_OIDC_STATE` after logging in | More than one API process, or the API restarted mid-login (just retry) |
| "Akun Anda belum terdaftar" | Section 5 not done for this stack, or the email differs from the Zitadel account |
| Web app loads but every request fails | Built with the wrong `API_DOMAIN`. Fix `.env`, run `./deploy.sh` again |
| `deploy.sh` says `ilb-edge is missing` | The proxy is not up. Section 4 |

Useful commands, from a stack's `deploy/`:

```bash
docker compose ps
docker compose logs -f api        # or web, db
docker compose restart api
```

## 7. API settings

For reference. `compose.yml` already sets these, only the ones marked `.env`
come from the env file.

| Variable | Value | Notes |
|---|---|---|
| `APP_ENV` | `staging` (dev) or `production` (prod) (`.env`) | Both make the API refuse to start with fake login |
| `DEBUG` | `false` | |
| `DATABASE_URL` | built from `POSTGRES_PASSWORD` (`.env`) | The `+psycopg` driver prefix is required |
| `CORS_ORIGINS` | `["https://" + WEB_DOMAIN]` (`.env`) | JSON list. The first entry is also where users land after login and logout |
| `AUTH_OIDC_MODE` | `zitadel` | |
| `PUBLIC_BASE_URL` | `"https://" + API_DOMAIN` (`.env`) | The Zitadel redirect URI is derived from it |
| `ZITADEL_ISSUER` | `https://auth8.veritask.ai` (`.env`) | No trailing slash |
| `ZITADEL_CLIENT_ID` | client ID of the IndoLegalBench Zitadel app (`.env`) | Not a secret for a public PKCE client, but kept out of this public repo |
| `COOKIE_SECURE` | `true` | Requires HTTPS |
| `IDLE_TIMEOUT_MINUTES` | `30` | |
| `ABSOLUTE_SESSION_LIFETIME_MINUTES` | `720` | 12 hours |
| `CREDENTIAL_ENCRYPTION_KEY` | Fernet key (`.env`) | See section 3. Never change it on a running install |

Left unset on purpose: `ZITADEL_CLIENT_SECRET` and `ZITADEL_AUDIENCE` (public
client with PKCE, audience defaults to the client ID) and `AUTH_DONE_URL_OVERRIDE`
(local testing only; if set, logins never return to the web app).

## 8. Backups

`backup.sh` dumps one stack's database to its `BACKUP_DIR` and keeps 7 days.
Schedule it once per stack:

```bash
sudo mkdir -p /var/backups/indolegalbench /var/backups/indolegalbench-dev
sudo chown "$USER" /var/backups/indolegalbench /var/backups/indolegalbench-dev
crontab -e
# add:
0 2 * * * /opt/indolegalbench/IndoLegalBench-server/deploy/backup.sh >> /var/backups/indolegalbench/backup.log 2>&1
15 2 * * * /opt/indolegalbench-dev/IndoLegalBench-server/deploy/backup.sh >> /var/backups/indolegalbench-dev/backup.log 2>&1
```

The VM clock is UTC+8, so `0 2` is 01:00 WIB.

The dumps stay on the VM, so they cover "we broke the data", not "the VM is
gone". They also do not include `CREDENTIAL_ENCRYPTION_KEY`; a restore on a new
VM needs the copy of the key kept in section 3. A VM snapshot on Veritask's
side, or copying the dumps to a bucket, covers the second case.

Restoring a dump replaces the current data, from that stack's `deploy/`:

```bash
gunzip -c /var/backups/indolegalbench/indolegalbench-YYYY-MM-DD.sql.gz \
  | docker compose exec -T db psql -U indolegalbench -d indolegalbench
```

## 9. Continuous deployment of dev

`.github/workflows/deploy-staging.yml`, in both repos, SSHes into the VM after
every push to `staging` and runs the dev `deploy.sh`. `deploy.sh` pulls both
repos, so a merge in either repo deploys the latest of both. Without the
secrets below the job skips with a notice instead of failing.

Set up once:

1. Make a key pair used for nothing else, on your own machine:

   ```bash
   ssh-keygen -t ed25519 -N "" -C github-deploy-staging -f ilb-deploy-staging
   ```

2. On the VM, add the public key to `~/.ssh/authorized_keys` of `ubuntu`,
   restricted to the dev deploy and nothing else (one line):

   ```
   restrict,command="/opt/indolegalbench-dev/IndoLegalBench-server/deploy/deploy.sh" ssh-ed25519 AAAA... github-deploy-staging
   ```

   `restrict` turns off port forwarding, agent forwarding and the terminal.
   `command=` makes SSH run the dev deploy whatever the client asks for, so a
   leaked key can only redeploy `staging`.

3. In **both** repos, Settings, Secrets and variables, Actions, add:

   | Secret | Value |
   |---|---|
   | `STAGING_DEPLOY_SSH_KEY` | the private key file `ilb-deploy-staging` |
   | `STAGING_DEPLOY_HOST` | `ubuntu@VM_IP` |
   | `STAGING_DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 VM_IP` |

   Organization secrets limited to the two repos work too. Then delete the
   private key from your machine.

4. Re-run the latest "Deploy staging" run, or use "Run workflow", and check
   the dev domains.

## 10. Releasing to prod

1. Open a PR from `staging` to `main` in each repo that changed, titled
   `release: <date or sprint>`. Merge them with a merge commit, not squash, so
   `main` keeps `staging`'s history. A squashed release leaves `main` with
   commits `staging` never had, and every later release PR conflicts. The repo
   setting "Allow merge commits" must be on for this; feature PRs into
   `staging` stay squash-only by convention.
2. Take a backup, then deploy prod by hand:

   ```bash
   cd /opt/indolegalbench/IndoLegalBench-server/deploy
   ./backup.sh && ./deploy.sh
   ```

3. Run section 6 on the prod domains.

A hotfix goes into `staging` first like any other change, then through the
same release PR.

## 11. Moving from the single-stack layout (once)

Before the split, one compose project `deploy` ran Caddy, API, web app and
database for the prod domains. These steps keep its data and certificates and
cost about a minute of downtime on prod. Do them outside working hours.

```bash
cd /opt/indolegalbench/IndoLegalBench-server
git pull --ff-only origin staging      # brings in the new deploy/ files
cd deploy

# 1. prod .env: add the new keys (section 3 table, prod column).
nano .env
#    COMPOSE_PROJECT_NAME=deploy, STACK=prod, APP_ENV=production,
#    DEPLOY_BRANCH=main, BACKUP_DIR=/var/backups/indolegalbench

# 2. Proxy .env.
cp proxy/.env.example proxy/.env

# 3. Build the prod images while the old stack still serves.
#    The client checkout is not pulled, so prod's web app stays as it is.
./backup.sh
docker compose build

# 4. Switch over (the downtime starts here).
docker rm -f deploy-caddy-1             # the old Caddy, frees 80 and 443
(cd proxy && docker compose up -d)
docker compose up -d --remove-orphans

# 5. Check.
docker compose ps
(cd proxy && docker compose ps)
curl -s https://api.indolegalbench.veritask.ai/health
```

The Caddy in `proxy/` reuses the volumes `deploy_caddy_data` and
`deploy_caddy_config`, so it keeps the existing certificates and only requests
new ones for the two dev domains.

Then set up dev: section 2 (the `/opt/indolegalbench-dev` checkouts only),
section 3 for dev, `./deploy.sh`, section 5, the dev line in section 8, and
section 9.

Until the first release (section 10), prod's `main` is behind `staging`, so do
not run prod's `deploy.sh` before that release is merged.

## Not covered yet

- `/docs` and `/openapi.json` are public on both stacks. Worth closing on prod.
- Sessions that simply expire in the browser are never deleted from the
  database. Harmless for now, needs a periodic cleanup later.
- Monitoring. Veritask's own Tencent-based infra is the plan for that.

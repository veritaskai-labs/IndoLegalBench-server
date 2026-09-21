# IndoLegalBench Server

Backend API for IndoLegalBench, the platform for writing and measuring legal test cases for Veritask.

The frontend lives in a separate repo: `IndoLegalBench-client` (Next.js).

## Architecture

Modular monolith. One deployment unit, split into domain modules, each module layered internally.

```
app/
  modules/              one folder per domain
    health/             the complete reference pattern, read this one first
    auth/               PBI-1  Secure login & team access management
    suites/             PBI-2  Managing suites as containers for cases
    cases/              PBI-3  Writing structured legal cases
    providers/          PBI-10 Registry of the AI products being measured
    runs/               Sprint 3, measurement execution
    reports/            Sprint 4, comparison reports
    audit/              Sprint 4, audit trail
  shared/               used across modules
    config.py           all application settings
    database.py         engine, session, Base
    exceptions.py       domain exceptions
    security.py         user roles and RBAC
    pagination.py       uniform pagination shape
  main.py               router registration and exception handlers

migrations/             Alembic
tests/                  mirrors the structure of app/modules/
```

### What each module contains

| File | Its job | May touch |
|---|---|---|
| `router.py` | Translate HTTP into service calls | its own module's service |
| `service.py` | Business logic | its own repository, other modules' services |
| `repository.py` | Database queries | its own module's models |
| `models.py` | SQLAlchemy tables | Base from shared |
| `schemas.py` | Request and response shapes, source of the OpenAPI contract | Pydantic |

### Two module boundary rules

1. A module may call another module's `service.py`. A module **may not** import another module's `repository.py` or `models.py`.
2. `service.py` must not touch HTTP. No `Request`, `Response`, or `HTTPException` in there. Raise exceptions from `app/shared/exceptions.py` and let `main.py` translate them into HTTP.

Keep those two rules and your modules have real boundaries, not just decorative folders.

## Running locally

There are two ways. Pick one.

### Quick way: the whole stack via Docker

```bash
docker compose up -d --build
docker compose exec api alembic upgrade head
```

The API comes up at http://localhost:8000. Good if you just need a live server, for example someone on frontend who needs the backend running.

Migrations deliberately do not run automatically on container start, so nobody changes the database schema without noticing.

### Development way: Python on the host, database in Docker

Use this when you are actually coding the backend, because `--reload` is much nicer than rebuilding the image on every line.

#### 1. Start the database

```bash
docker compose up -d db
```

If you are not using Docker, set up PostgreSQL yourself and adjust `DATABASE_URL` in `.env`.

#### 2. Set up the environment

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pre-commit install
cp .env.example .env
```

Then fill in `.env` as needed.

`pre-commit install` is once per clone. After that every commit is scanned by detect-secrets, so credentials do not get committed. CI scans again, but catching it locally is cheaper.

#### 3. Run migrations

```bash
alembic upgrade head
```

#### 4. Seed the local test accounts

```bash
python scripts/seed_dev.py
```

Creates four accounts, one per role, so you can exercise RBAC without a
working Zitadel login. Safe to run more than once, existing emails are
skipped. These are development accounts only and deliberately live
outside the migrations, so they never reach staging or production.

| Email | Role |
|---|---|
| `author@veritask.test` | author |
| `reviewer@veritask.test` | reviewer |
| `admin@veritask.test` | admin |
| `viewer@veritask.test` | viewer |

Their `zitadel_sub` is left empty on purpose. The accounts are registered
on the platform but have never logged in, which is the same state an
account is in right after an Admin creates it.

#### 5. Run the server

```bash
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- OpenAPI contract: http://localhost:8000/openapi.json
- Health check: http://localhost:8000/health

## Daily commands

```bash
pytest                          # run the whole test suite
pytest tests/modules/suites     # test a single module
ruff check .                    # lint check
ruff format .                   # tidy formatting
ruff check . --fix              # fix the lint issues that can be fixed automatically
python scripts/export_openapi.py  # regenerate the contract after changing schemas.py
python scripts/seed_dev.py        # create the four local test accounts
```

CI runs all of the above too. Run them locally before pushing so your PR does not go red.

## Creating a new migration

```bash
alembic revision --autogenerate -m "add cases table"
alembic upgrade head
```

**Important:** whenever a new module has a table, add its model import to `migrations/env.py`. Forget that and Alembic will not see the table, and autogenerate will produce a wrong migration.

Always read the autogenerated migration file before committing it. Alembic often guesses wrong, especially for column type changes and renames.

## Adding a new module

1. Create the folder at `app/modules/<name>/`
2. Copy the five files from an existing module: `router.py`, `service.py`, `repository.py`, `models.py`, `schemas.py`
3. Register its router in `app/main.py`
4. If it has a table, add the model import to `migrations/env.py`
5. Create the mirroring test folder at `tests/modules/<name>/`

## The OpenAPI contract

The contract is generated automatically by FastAPI from the Pydantic schemas in each module. The `schemas.py` files are the source of truth.

If you change `schemas.py` or add an endpoint, the contract changed. What you must do:

1. Run `python scripts/export_openapi.py` and commit `openapi.json` in the same PR
2. Announce in the group chat that the contract changed, say which part
3. Whoever is on frontend re-runs the TypeScript type generator

CI rejects any PR that skips step 1. Step 2 is the one people forget most, and it is the most common reason the frontend suddenly breaks with nobody knowing why.

**For people on frontend:** the latest contract is available as the `openapi-contract` artifact on every CI run. Open the Actions tab, pick a run on `staging`, download it from the Artifacts section. No need to run a Python server on your machine.

## Rules for secret files

Never commit `.env`, credentials, API keys, or Zitadel keys.

If a credential is committed by accident, do not just delete it in the next commit, because Git history still holds it. Tell the team immediately so the credential can be revoked and replaced.

This applies extra strictly to the `providers` module, because what is handled there are the client's competitor AI product credentials.

## Git workflow

Read `CONTRIBUTING.md`.

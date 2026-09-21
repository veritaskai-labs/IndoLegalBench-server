# IndoLegalBench Contributing Guide

This document covers both repos: `IndoLegalBench-client` (Next.js) and `IndoLegalBench-server` (FastAPI).
The point is that everyone works the same way and nobody has to ask how to start.

If a rule here gets in your way, do not quietly break it. Raise it at daily or in the group chat, and we change the document together.

---

## 1. Branch Structure

There are only two permanent branches. Everything else is short-lived and deleted after merge.

| Branch | Contents | Who may write |
|---|---|---|
| `main` | Code running in production | Nobody pushes directly. Merges from `staging` only |
| `staging` | Daily integration, deployed to staging | Nobody pushes directly. Merges from sub task branches via PR only |
| `<type>/<pbi>-<description>` | One person's work on one sub task | The branch owner |

**The default PR target is `staging`.** Do not open a PR against `main` unless you are actually cutting a release.

### What goes where, and when

- Sub task done, review passed, CI green, then merge into `staging`
- A whole PBI done and passing Definition of Done, then `staging` merges into `main`
- At Sprint Review, `main` gets tagged with the release version

Do not wait for an entire PBI to finish before merging into `staging`. Sub tasks that are done can and should land first.

---

## 2. Branch Naming

Format:

```
<type>/<pbi>-<short-description>
```

Examples:

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

The types we use, following Conventional Commits:

| Type | What for |
|---|---|
| `feat` | New user-visible feature |
| `fix` | Bug fix |
| `chore` | Setup, configuration, dependencies, migrations |
| `ci` | Pipeline and automation |
| `docs` | Documentation, ERD, contract |
| `test` | Adding or fixing tests |
| `refactor` | Tidying code without changing behaviour |

**Important rule:** if one sub task touches both repos, use the exact same branch name in `client` and `server`. This is so that at Sprint Review we can trace one sub task to both of its PRs.

Always include the PBI code in the branch name. Without it, we cannot prove which PBI is actually Done.

---

## 3. Commit Format

Use Conventional Commits, the same as what is already running in the client repo.

```
<type>(<optional scope>): <short description, lowercase, no trailing period>
```

Examples:

```
feat(case-editor): add inline validation for reference fields
fix(auth): correct redirect after Zitadel callback
chore(db): add migration for suites table
docs(api): update OpenAPI contract for cases endpoint
test(suite): add test rejecting duplicate suite names
```

Write commit descriptions in English. Explain what changed, not just "update" or "fix bug".

---

## 4. Day-to-day Workflow

Follow this order every time you pick up a new sub task.

**1. Take a sub task from the board**

Make sure nobody else has taken it, then assign it to yourself before you start coding.

**2. Make sure `staging` is current**

```bash
git checkout staging
git pull origin staging
```

**3. Create the branch**

```bash
git checkout -b feat/pbi3-case-editor
```

**4. Work, and commit in small pieces**

Do not pile all your work into one giant commit. Commit each time a logical piece is finished.

**5. Pull `staging` every morning while the branch is alive**

```bash
git checkout staging
git pull origin staging
git checkout feat/pbi3-case-editor
git merge staging
```

A small conflict every day is far cheaper than a big one at the end of the sprint.

**6. Push and open a Pull Request**

```bash
git push -u origin feat/pbi3-case-editor
```

Open the PR against `staging`, fill in the PR template, ask one person to review.

**7. Once approved, squash merge, then delete the branch**

GitHub has a button for both. Squash keeps the `staging` history clean, one commit per sub task.

---

## 5. Non-negotiable Rules

These five matter most for whether our sprint survives.

**1. A branch lives 2 to 3 days at most.**
If your sub task needs longer than that, the sub task is too big. Raise it at daily so it gets split, do not push through on your own.

**2. Always branch from the latest `staging`.**
Do not branch from someone else's branch unless there is a direct dependency (see section 7).

**3. No direct pushes to `main` or `staging`.**
Everything goes through a Pull Request, no exceptions, including one-line fixes.

**4. Every PR needs one approval and green CI.**
Do not merge your own PR unreviewed. Do not approve without actually reading.

**5. Oversized PRs will be sent back to be split.**
If your PR changes more than roughly 400 lines, consider splitting it. A reviewer cannot read a giant PR seriously, and a careless review is the same as no review.

---

## 6. The OpenAPI Contract

The API contract is the single source of truth between client and server. Treat it seriously.

**Where the contract lives:** the `IndoLegalBench-server` repo. FastAPI generates it automatically from the Pydantic models, and the result is committed to the repo so changes show up in the PR.

**If you change a schema or an endpoint:**

1. Change the Pydantic model on the server
2. Regenerate the contract file and commit the result in the same PR
3. **Announce it in the group chat**, say which part changed
4. Whoever is on frontend re-runs the TypeScript type generator

Step 3 is the one people forget, and it is the most common reason the frontend suddenly breaks with nobody knowing why.

**If you are on frontend and need an endpoint that does not exist yet:**
Do not wait. Agree the shape of the contract with someone on backend first, then build your page against mock data. Once the real endpoint lands, you just wire it up.

**Special note for PBI-3:** the legal case schema (OpenAPI Case) is used for four things at once, namely backend validation, real-time frontend validation, the export format for AiYU, and the API documentation. Validation rules are written once in the schema, not rewritten separately in the frontend. If you are tempted to copy validation rules into the frontend by hand, stop and ask first.

---

## 7. Handling Dependencies Between Sub Tasks

Some sub tasks cannot start before others finish. Here is how to handle it.

**Foundation sub tasks are done first and are not part of the free pick-up system:**

- `[BE] Setup repository & CI`
- `[BE] First DB schema & migration`
- `[FE] Frontend bootstrap & generate from contract`
- `[SA] Case schema contract (ERD)`

All four need to land in `staging` as early in the sprint as possible. Until they do, most other sub tasks are blocked.

**If your sub task depends on a branch that is not merged yet:**

Do not sit idle waiting. Branch from that branch, do your part, then rebase onto `staging` once the dependency lands.

```bash
git checkout feat/pbi1-admin-members-endpoint
git checkout -b feat/pbi1-admin-members-page
# do the work
# once the dependency branch is merged into staging:
git checkout staging && git pull
git checkout feat/pbi1-admin-members-page
git rebase staging
```

**If you are blocked for more than half a day,** raise it in the group chat. Do not wait in silence, pick up another sub task that is not blocked.

---

## 8. Definition of Done

A PBI may only be called Done when all six of these are met. This applies equally to PBI-1, PBI-2, PBI-3, and PBI-10 because this is the team standard, not a per-item standard.

- [ ] **Design Reviewed** — the design was reviewed by the team before coding
- [ ] **Code Completed** — all BE, FE, and SA sub tasks are finished and in `staging`
- [ ] **Tested** — unit and integration tests run, coverage stays above 60 percent
- [ ] **No Blocker Bugs** — nothing breaks the main flow
- [ ] **Accepted by PO** — approved by the Product Owner
- [ ] **Live on Production** — actually shipped, not just running locally

A finished sub task does not mean the PBI is Done. PBI Done is a shared decision at the end, not an individual claim.

---

## 9. Releasing

Once a PBI has passed the entire Definition of Done:

1. Open a PR from `staging` to `main`
2. After it merges, deploy to production
3. At Sprint Review, tag `main`

```bash
git checkout main
git pull origin main
git tag -a v1.0 -m "Sprint 1 Release: Foundation and Output Contract"
git push origin v1.0
```

Decide up front who owns this release step. Do not decide it on the last day.

---

## 10. Initial Setup

### Client (`IndoLegalBench-client`)

```bash
git clone <client-repo-url>
cd IndoLegalBench-client
npm install
cp .env.example .env.local   # fill in as needed
npm run dev
```

### Server (`IndoLegalBench-server`)

```bash
git clone <server-repo-url>
cd IndoLegalBench-server
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # not requirements.txt, this one includes pytest and ruff
pre-commit install           # once per clone, enables credential scanning
cp .env.example .env         # fill in as needed
docker compose up -d         # local PostgreSQL
alembic upgrade head
uvicorn app.main:app --reload
```

The automatic API documentation is available at `http://localhost:8000/docs` once the server is running.

How to run tests, create migrations, and regenerate the OpenAPI contract is in the `README.md` of the server repo.

### Rules for secret files

Never commit `.env`, credentials, API keys, or Zitadel keys. Make sure those files are in `.gitignore`.

If a credential is committed by accident, **do not just delete it in the next commit**, because Git history still holds it. Tell the team immediately, then the credential has to be revoked and replaced.

This applies extra strictly to PBI-10, because what is handled there are the client's competitor AI product credentials.

---

## 11. When in Doubt

- Unsure about something technical, ask in the dev group chat
- Unsure about the scope of a sub task or its acceptance criteria, ask the PO
- Unsure about a rule in this document, raise it at daily so the document gets fixed

Asking for five minutes is cheaper than building the wrong thing for two days.

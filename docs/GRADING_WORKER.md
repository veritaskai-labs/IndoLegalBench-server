# Grading worker

SCRUM-284 (client UAT feedback K-13) moves grading out of the HTTP request.

## Starting a grade

`POST /measurements/{id}/grading-runs` records a `queued` grading run and answers 202 right away. The run has its judge, rubric version and kind, and a `grading_run_progress` row with the number of attempts to grade.

## Running the worker

Run it with `python -m app.grading_worker`. The deploy Compose stack has a private `grading-worker` service that uses the API image and database. It has no HTTP port and is not on the edge network.

It is a separate process from `app.measurement_worker`. That way, a long grading run does not hold up measurement sessions, and a measurement session does not hold up grading.

Every 5 seconds when idle (straight away when busy), one round does this:

1. **Recover.** A `running` run whose `heartbeat_at` is 300 seconds old or more is marked `failed` with `error_code = GRADING_INTERRUPTED`. One judge call is capped at 240 seconds and the heartbeat is refreshed after every attempt, so 300 seconds without a heartbeat means the worker died.
   - The run is not resumed automatically, because judge calls that were already sent may have been billed. This follows the same idea as SCRUM-268.
   - The Admin starts a new grade instead.
2. **Claim.** Take the oldest `queued` run with `FOR UPDATE SKIP LOCKED`, mark it `running`, and stamp `started_at` and `heartbeat_at`.
3. **Grade.** Call the judge per attempt, the same way `POST /grade` does. After each attempt, `attempts_graded` and `heartbeat_at` are committed, so `GET /measurements/{id}/grading-progress` moves forward.
4. **Save.** The results are written to the same run.
   - If at least one attempt got a score, the run ends `finished`, becomes the latest result, and a complete manual session moves to Dinilai.
   - If no attempt got a score, the run ends `failed` with `NO_SCORE` and does not replace the latest result.

## Error codes on `grading_run_progress.error_code`

| Code | Meaning |
|---|---|
| `NO_SCORE` | No attempt got a score. Causes include the judge failing, the judge being unavailable, or missing token prices. |
| `SESSION_ARCHIVED` | The session was archived while grading ran. No results were saved. |
| `ANSWERS_CHANGED_DURING_GRADING` | A manual answer changed while grading ran. No results were saved. |
| `GRADING_ERROR` | Any other error. Only the error type is logged, never its message, because provider messages can contain secrets. |
| `GRADING_INTERRUPTED` | The worker stopped during the run (stale heartbeat). |

## One active run per session

The partial unique index `uq_grading_runs_one_active` (status `queued` or `running`) allows one active run per session. `POST /grade` and `POST /grading-runs` both answer 409 `GRADING_IN_PROGRESS` while a run is active.

## Deploying

`deploy/deploy.sh` stops `grading-worker` together with `worker` before migrations run.

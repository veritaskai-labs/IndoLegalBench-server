# Automatic measurement worker

SCRUM-249 connects the existing queue, frozen inputs and provider loop to durable
attempts/call logs. Start it with `python -m app.measurement_worker`; the deploy
Compose stack includes a private `worker` service using the API image and database.
The worker has no HTTP port and is not attached to the edge network. On boot it
waits for SCRUM-217 to resolve a pre-existing running session; it never silently
auto-resumes one. A duplicate worker process exits if another owns the lease.

The worker uses the SCRUM-214 status contract merged in PR #97. Later transition
and recovery work must integrate with that contract rather than duplicate it.

The queue creates snapshot items and pending attempts. The store orders snapshot
items by case ID and snapshot-item ID, claims each pending attempt durably before
network I/O, then commits the answer and its correctly linked call log together.
Successful attempts are skipped on resume; technical failures are not automatically
retried. SCRUM-218 must explicitly make selected failed attempts pending to dispatch
them again, keeping their sample repetition number and previous call logs.

A PostgreSQL advisory lease allows one dispatcher per database even across process
restarts/replicas. A running attempt left after a crash is uncertain: dispatch raises
a recovery-required error rather than resending a potentially paid call. SCRUM-217
pauses abandoned running sessions; SCRUM-268 (option A) marks their orphan running
attempts as `technical_failure` (see "Interrupted call recovery" below).
This worker does not implement the recovery decision or heartbeat itself.
When resume checks whether an in-flight dispatcher is active, the HTTP request
briefly probes this same advisory lease. If an idle worker tick overlaps a successful
probe, that tick can defer dispatch until its next poll (normally 5 seconds).
The probe releases the lease before returning and never dispatches paid work.

Controls must use SCRUM-214 transitions. The dispatcher checks the session again
before every call. SIGTERM/SIGINT lets the current call persist, then pauses the
session with reason `restart`; the 260-second Compose grace period exceeds the
provider's 240-second timeout. Hard kills still require SCRUM-217 recovery.

SCRUM-216 adds `POST /measurements/{id}/pause`, `/cancel` and `/resume`.
Controls default to Admin-only. `MEASUREMENT_DEV_AI_CONTROLS_ENABLED=false` keeps
Developer AI view access unchanged; if explicitly enabled, only sessions with
`created_by` equal to that Developer AI's user ID can be controlled. An Admin
must still provide any required unknown-version confirmation.
Pending formal PO sign-off on D10, the implemented conservative rollout is Admin
by default for pause/cancel/resume, with own-session Developer AI access only
when the explicit flag is enabled. Retry (#99) should
align with this rollout rule; unknown-version confirmation remains Admin-only.
SCRUM-216 implements pause/cancel/resume. The retry owner plans to align its
separate endpoint with this proposed policy
([SA rereview](https://github.com/veritaskai-labs/IndoLegalBench-server/pull/102#pullrequestreview-5785387291));
PBI acceptance still requires the PO permission decision. Archive is a separate
operation; its own-session ownership rule is reused here without broadening execution rights.
Pause and cancel return 202 with `{id, status, stopped_at, stop_reason}` after the control
transaction commits. The state blocks the next dispatch; an already claimed call
may still be finishing and will keep its answer, call log and cost. `stop_reason`
is `user` for both actions. GET detail reloads the persisted stop metadata.
Repeated or invalid actions return 409 `INVALID_TRANSITION`; archived sessions
return `SESSION_ARCHIVED`, accepted sessions `SESSION_ACCEPTED`, and manual sessions
`SESSION_NOT_AUTOMATIC`. Cancellation while queued is allowed by the merged D4.
Pause then cancel and never-started requeue are approved in the
[SCRUM-216 SA review](https://github.com/veritaskai-labs/IndoLegalBench-server/pull/102#pullrequestreview-5783940566).

Resume returns 202 `{id, status, resumed_after_long_pause, model_version_unverified}`.
It reopens a prepared session as `running`; the same leased dispatcher processes
pending attempts and preserves successful/failed attempts. A cancelled session
that never started returns to `queued`, so original preparation creates its first
snapshot/copy. Its new queued_at intentionally puts it at the back of the queue.
An occupied running slot returns 409 `ANOTHER_SESSION_RUNNING` with
no partial writes. Repeated resume is 409 `SESSION_NOT_RESUMABLE`. A running attempt
without a live dispatcher lease is an orphan: resume marks it `technical_failure` in the
same transaction (SCRUM-268) instead of returning 409. No automatic retry is introduced.

The version source is the last successful call/attempt of the same product across
sessions, ordered by ended_at, started_at, then ID. Only provider-reported response
`model` is recorded as measurement version; a configured alias is not substituted.
A known difference from `model_version_at_start` rejects resume with 409
`MODEL_VERSION_CHANGED`. No discovery call is made. Unknown versions permit resume
with `model_version_unverified`; if successful calls exist but none has a version,
the API first returns 409 `MODEL_VERSION_CONFIRMATION_REQUIRED`. Admin can explicitly
resubmit `{confirm_unverified_version: true}`. Developer AI cannot confirm this.

The worker keeps version verification pending until a response carries a version.
A versionless success or technical failure sets the unverified marker and does not
consume the check, even if every remaining response lacks a version. The first
version-carrying response is compared in the worker transaction. If it reveals a changed version, its attempt/result/log/cost are still saved, the session
pauses with reason `error`, and GET detail exposes `resume_error_code=MODEL_VERSION_CHANGED`.
The earlier HTTP 202 cannot change retrospectively; subsequent resume requests are
409 and a new session is required. A concurrent user pause/cancel keeps its state
and reason while retaining the version error. A null baseline is initialized once
when a response first carries a version; absence remains explicit unknown evidence.

`RESUME_LONG_STOP_DAYS` defaults to 7 (non-negative integer); interval is measured
from the previous stopped_at in UTC. Strictly greater than the limit sets
`resumed_after_long_pause`, without rejecting resume. Both markers persist in GET
detail and remain true as historical evidence on later resumes. Legacy missing
stopped_at cannot establish elapsed duration and is not assigned a guessed date.
The two configuration values are forwarded by local/deploy Compose to the API.
No actual environment/credential file is changed by this implementation.

Apply migration `e216a5b7c901` and deploy matching API/worker code together. Downgrade
refuses while resume verification/marker evidence exists rather than silently
discarding a model-version block. Preserve that evidence and reconcile with the
owner before rollback. The migration follows staging revision `e7a41c2b9d53` from #104. Whichever of
this change and #99 lands second must reconcile its Alembic parent with final staging.

Final status and the starter's in-app notification commit atomically through the
existing notifier contract. Repeating finalization does not send a second message.
Budget pauses send no completion notification. All processed calls failing still
means `finished_with_failures`, preserving the retry path. The open SA/PO question
about fatal errors mid-run is not assigned an invented status by this runtime.

Model, endpoint, parameters and prices use the frozen product copy; the current
encrypted credential is opened only by providers.service. The operational rate
limit is read when the dispatch batch starts because D2 has no frozen rate-limit
column. The operational monthly budget follows the live product, so raising the
budget can permit resume and lowering it affects the next budget check. Prices
and model inputs remain frozen. The first reported model version fills
model_version_at_start through migration d49a1f2b3c60, which permits only a one-time
initialization backed by an observed attempt and preserves all other immutability. Partial
failed answers and sanitized errors are retained in call_logs.raw._execution;
attempts.answer_text is reserved for immutable successful answers so failed calls
remain retryable under the existing PostgreSQL trigger.

SCRUM-250 owns completion of deployment cost settings. Before paid dispatch, the
runtime checks the exchange rate and resolves both token prices (frozen values,
then the existing configuration catalog). Never use fabricated prices/rates to
make a measurement proceed. The worker needs the same applicable cost settings
and daily-rate configuration as the API. No `.env` file or actual credential is
included in this change.

See [MEASUREMENT_COST_SETUP.md](MEASUREMENT_COST_SETUP.md) for the shared API/worker
environment contract, approved-value requirements and non-secret dev smoke checks.

Verification: run the runtime integration tests with a controlled provider, then
repeat against migrated PostgreSQL, including enum/trigger and advisory-lock
checks. Deployed laptop/physical-phone QA and a client-authorized small real-AI
run remain required before UAT acceptance.

## Interrupted call recovery (SCRUM-268, option A)

A hard kill during a provider call leaves the claimed attempt `running`. The call_logs
row for that try is written only after the response returns, so usually no row exists;
an attempt that was retried before keeps the rows of its earlier tries.

- API startup (`recover_after_restart`, only while the worker lease is free) pauses the
  session with reason `restart` and marks its running attempts `technical_failure` in one
  transaction. `finished_at` is the recovery time when `started_at` is known.
- Resume does the same for any orphan running attempt when no dispatcher holds the
  lease (sessions paused by the liveness check, cancelled sessions, or ones stuck before
  this fix). A rejected resume rolls back and writes nothing.
- No call_logs row or cost is invented for the interrupted try: its cost is unknown and
  is not counted toward the monthly budget. Existing rows stay unchanged.
- The session then finishes as `finished_with_failures`; the Admin chooses retry (the
  only way the call is sent again) or accept as is. Repeating recovery is a no-op.

## Manual recovery for a blocked running session

Until SCRUM-217 recovery is integrated, an uncertain attempt or fatal error after
partial execution leaves the session running. Its logical running slot blocks
queued sessions, although the PostgreSQL dispatcher lease is released on exception.
The supervisor retries after five seconds; ERROR logs identify the session UUID
and exception class without logging provider text or a traceback. This is an
operator intervention condition, not permission to resend the call.

1. Find the session UUID in the ERROR log and stop the worker with
   `docker compose stop worker` from `deploy/`. Let any active call finish.
2. Inspect that session's attempt statuses and call-log metadata through the
   authorised operator tools. Keep all answers, attempts and previous call logs.
   Do not copy raw provider responses or credentials into tickets or logs.
3. If an attempt is still running, do not reset it to pending merely to unblock
   the queue: that may repeat a paid call. Pausing the session (step 4) and then
   resuming lets SCRUM-268 mark it `technical_failure`; only an Admin retry sends it again.
4. Correct the operational cause (credential availability, prices, rate source
   or database write failure) without changing frozen model inputs or answers.
   After reconciliation and operator approval, use the existing
   `SessionStatusRepository.change_status` transition to `paused` with reason
   `restart` and the current UTC timestamp, then restart the worker. This records
   an operator stop and releases the running slot; it does not assign a new
   fatal-error terminal status. Never bypass the transition service with SQL.
5. Resume only through the authorised SCRUM-216 flow and only
   after the cause and uncertain attempts are resolved. The runtime skips
   successful attempts; explicit failed-only retry remains SCRUM-218's scope.

SA/PO still owns the terminal-status policy for fatal errors after partial
execution. Escalate unresolved cases with the session UUID and safe error class;
do not invent a status or auto-retry uncertain calls. The one-time model-version
trigger exception in migration `d49a1f2b3c60` was approved by the SA against
D2/D3/D5 in the [PR #98 SA review](https://github.com/veritaskai-labs/IndoLegalBench-server/pull/98#issuecomment-6652764014).
The attempt is explicitly flushed before the copy update so its version evidence
exists regardless of ORM table ordering. SCRUM-216 must treat a null initial model
version as unknown when evaluating resume; it is not proof of a version match or change.

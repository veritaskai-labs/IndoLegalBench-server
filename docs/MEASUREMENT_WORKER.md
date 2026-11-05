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
must resolve those attempts and pause abandoned running sessions before allowing
resume. This worker does not implement the recovery decision or heartbeat itself.

Controls must use SCRUM-214 transitions. The dispatcher checks the session again
before every call. SIGTERM/SIGINT lets the current call persist, then pauses the
session with reason `restart`; the 260-second Compose grace period exceeds the
provider's 240-second timeout. Hard kills still require SCRUM-217 recovery.

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
3. If an attempt is still running, have the SCRUM-217 owner reconcile whether
   the provider processed it. Do not reset it to pending merely to unblock the
   queue: that may repeat a paid call. Keep the worker stopped until reconciled.
4. Correct the operational cause (credential availability, prices, rate source
   or database write failure) without changing frozen model inputs or answers.
   After reconciliation and operator approval, use the existing
   `SessionStatusRepository.change_status` transition to `paused` with reason
   `restart` and the current UTC timestamp, then restart the worker. This records
   an operator stop and releases the running slot; it does not assign a new
   fatal-error terminal status. Never bypass the transition service with SQL.
5. Resume only through the authorised SCRUM-216 flow once available and only
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

# Automatic measurement worker

SCRUM-249 connects the existing queue, frozen inputs and provider loop to durable
attempts/call logs. Start it with `python -m app.measurement_worker`; the deploy
Compose stack includes a private `worker` service using the API image and database.
The worker has no HTTP port and is not attached to the edge network. On boot it
waits for SCRUM-217 to resolve a pre-existing running session; it never silently
auto-resumes one. A duplicate worker process exits if another owns the lease.

The branch depends on SCRUM-214 PR #97. Merge/rebase on its final contract before
release. Do not merge the dependent status implementation separately from its owner.

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

Verification: run the runtime integration tests with a controlled provider, then
repeat against migrated PostgreSQL, including enum/trigger and advisory-lock
checks. Deployed laptop/physical-phone QA and a client-authorized small real-AI
run remain required before UAT acceptance.

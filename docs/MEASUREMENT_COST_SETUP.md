# Measurement cost configuration and dev smoke check

SCRUM-250 supplies the same cost environment to the API (summary/start validation)
and the automatic worker (billable-call preflight and actual cost). It depends on
the SCRUM-249 worker, merged in server PR #98. This
document does not declare deployment or UAT complete.

## Configuration contract

The deployment operator sets approved values in the existing protected stack
configuration. `deploy.sh` exports that configuration before running Compose.
The shared `measurement-cost-environment` mapping forwards the seven names below
to both processes. No API credential, real price, rate or client data belongs in
the repository. This change does not modify a credential file or introduce an
operational token/rate default.

Because `deploy.sh` sources the file as shell, wrap JSON values in single quotes
to preserve their internal double quotes, for example `COST_MODEL_PRICES='{}'`.
Use the commented entries in `deploy/.env.example`; replace empty catalogs with
approved prices when product-specific prices are absent. Keep unused optional
settings commented instead of assigning blank numeric/JSON values.

## Approved setup policy

The PO selected **1,000 tokens per answer** on 5 November 2026 as the initial
estimate, based on one medium conversation. The operator should explicitly set
`COST_DEFAULT_AVG_ANSWER_TOKENS=1000`; this is not a measured provider-specific
average. Real history continues to take precedence. The separate mention of
8,000 tokens does not establish a universal output limit or change model parameters.

The PO also confirmed that a failed live-rate refresh should reuse the last
successful rate until live retrieval succeeds again, without a specified maximum
age. Do not configure a fabricated static fallback. Keep
`COST_USD_IDR_FALLBACK_RATE` unset unless separately approved. With neither a live
rate nor a previous successful rate, retain `EXCHANGE_RATE_NOT_CONFIGURED`.

The existing daily-rate adapter already retries retrieval after a failed refresh,
keeps its last successful value and replaces it on success. However, that cache
is **process-local memory**: API and worker do not share it and a restart clears
it. A cold process during an outage cannot reuse another process's previous rate.
This PR forwards configuration; it does not implement shared/durable rate storage.
Record this limitation during review and dev smoke checks. Deployment readiness
must not imply that outage reuse across restarts/processes has been verified.

| Variable | Source and behavior |
|---|---|
| `COST_DEFAULT_AVG_ANSWER_TOKENS` | PO-selected initial value: `1000`, explicitly set by the operator. Real product history takes precedence. Unset means `AVG_ANSWER_TOKENS_NOT_CONFIGURED` when needed. |
| `COST_MODEL_INPUT_PRICES` | JSON object keyed by `provider_type:model_name`, USD per million input tokens. Used where the product has no input price. |
| `COST_MODEL_PRICES` | Same JSON key format, USD per million output tokens. Used where the product has no output price. |
| `COST_SAFETY_MARGIN_PERCENT` | Nonnegative estimate margin. Existing application default is 20 percent; override only using the agreed policy. |
| `COST_USD_IDR_FALLBACK_RATE` | Leave unset under the current policy: no static value was approved. The existing optional setting remains supported for a separately approved deployment. |
| `JISDOR_API_URL` | Existing daily-rate endpoint, or an approved replacement using the current adapter's response contract. Unset retains the Settings URL. |
| `JISDOR_API_KEY` | Daily-rate service credential, supplied securely to both processes. Without it the adapter does not fetch and uses its fallback. Never print or record its value. |

Price maps must be valid JSON with nonnegative decimal values; negative prices
are invalid. Zero is valid only for an actually free model, not as a way to pass
preflight. Product prices override the catalogs. Runtime prices are frozen in
the product copy; the operational budget follows the current product.

Omit an optional setting to retain the application default. Do not supply an
empty string for a decimal or JSON setting: startup validation rejects it.
Compose's null pass-through entries leave absent values out of the container
environment. Docker Compose tests cover both exported host variables and an
explicit configuration-file source. Changing host configuration requires
recreating the applicable containers; restarting an old container alone does
not update its environment.

## Local evidence before deployment

Run from the server checkout:

```bash
pytest tests/deployment/test_measurement_cost_environment.py --no-cov
pytest tests/modules/runs/test_cost_settings.py tests/modules/runs/test_summary_service.py tests/modules/runs/test_summary_endpoint.py tests/modules/runs/test_start_endpoint.py tests/modules/runs/test_execution_runtime.py --no-cov
```

The Compose test uses synthetic values and starts no containers. The existing
HTTP/service tests cover no-history fallback, missing configuration, historical
averages, product/catalog prices and Start's budget revalidation. Runtime tests
use controlled providers; PostgreSQL cases require the dedicated migrated test
database. These checks do not establish that the deployed processes are configured.

CI also runs a dedicated verbose Compose test step after `docker compose version`.
Missing Docker/Compose fails that step, rather than silently counting the
configuration tests among the full suite's environment-specific skips. Its log
lists each host, file, shell-sourced-file and unset-setting case individually.

## Dev readiness and smoke check

1. Record the merged server/client commits, deployed server revision and review
   approval. Verify the final status/runtime migrations are deployed. Use only
   the `dev` stack; do not run smoke tests against production.
2. Record approval/source/date for the initial answer-token estimate and the
   permitted rate/fallback policy. Check both product token prices or the
   matching catalogs, the actual remaining budget, and the selected model's
   provider type. A missing catalog entry is acceptable only if that product
   supplies the corresponding price.
3. Have the operator apply the approved configuration through the established
   deployment process. Do not paste raw `docker compose config` or environment
   output into evidence: those can contain credentials and database passwords.
4. Verify API and worker presence using the following read-only check, once for
   each process. It prints only presence/counts, never setting values or keys:

   ```bash
   docker compose exec -T api python - <<'PY'
   from app.shared.config import get_settings
   s = get_settings()
   print({
       "initial_answer_tokens_configured": s.cost_default_avg_answer_tokens is not None,
       "input_catalog_entries": len(s.cost_model_input_prices),
       "output_catalog_entries": len(s.cost_model_prices),
       "fallback_rate_configured": s.cost_usd_idr_fallback_rate is not None,
       "daily_rate_credential_configured": bool(s.jisdor_api_key),
   })
   PY
   ```

   Repeat with `worker` instead of `api`. Presence alone does not prove the rate
   endpoint works, the model's prices exist, or the estimate's basis is approved.
5. Use an active automatic product, Admin/Developer AI account and approved
   Veritask suite. The client/Admin enters its provider credential through the
   existing UI. Do not seed fabricated call history or invent legal questions.
6. On a product without history, request a summary. Record HTTP status, counts,
   filter, estimate and remaining budget, excluding question text and credentials.
   If settings are missing, confirm the UI displays the server message (client
   PR #69 owns that rendering). Do not bypass the missing-setting error.
7. With an explicitly approved small real-provider budget, confirm Start and
   observe queue → worker → persisted answer/call log → final status → one
   in-app notice to the starter. Inspect responses for the existing role/masking
   rules. The full deployed flow also belongs to SCRUM-213.
8. After genuine history exists, confirm the next summary uses history instead
   of the configured initial estimate. Test insufficient-budget rejection using
   the controlled integration suite or an approved disposable dev product;
   do not alter shared operational budgets without agreement.

Keep an evidence table with AC, expected/actual result, timestamp, commit,
non-secret configuration presence, approved-value reference, pass/fail/pending
and owner. Keep actual secret values and client test content out of public PRs.

## Remaining acceptance gates

The initial token basis and outage policy are approved, but deployment
configuration, a working rate source and a deployed smoke check are still
required before SCRUM-250 is complete. Cross-process/restart cache limitations
remain as described above. Local test
fixtures are not approved operational values. Pending PBI-12 controls/recovery
and PBI-14 scoring remain separate dependencies; do not mark all PBI-11 AC done
from this configuration change alone.

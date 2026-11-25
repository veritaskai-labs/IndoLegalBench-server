"""One log line per /auth/callback result (platform monitoring, no third-party tool).

The callback always answers 302, also on failure (router.py sends the code to
/auth/done?error=CODE), so this line is the only place a failed login is visible. It carries
the outcome and who has to act, never an email, a subject, the one-time code or the state.
Format is key=value, so any log tool can filter and count it.
"""

import logging

logger = logging.getLogger("app.auth.login")

# Error code -> (who has to act, log level).
_FAILURE_KIND = {
    # Token exchange with Zitadel failed: Zitadel down, wrong config, or server clock off.
    # Users cannot fix it and nobody can log in, so it is the one ERROR (alert trigger).
    "OIDC_EXCHANGE_FAILED": ("system", logging.ERROR),
    # Login state lost: old tab, double click, or an API restart (deploy) dropped the pending
    # login (pending.py keeps it in memory).
    "INVALID_OIDC_STATE": ("flow", logging.WARNING),
    # Account not registered or deactivated: an Admin acts, not the developers.
    "USER_NOT_REGISTERED": ("account", logging.INFO),
    "USER_DEACTIVATED": ("account", logging.INFO),
}


def log_login_outcome(error_code: str | None) -> None:
    """Write the result of one callback. None means the login succeeded."""
    if error_code is None:
        logger.info("login outcome=ok")
        return
    kind, level = _FAILURE_KIND.get(error_code, ("unknown", logging.ERROR))
    logger.log(level, "login outcome=%s kind=%s", error_code, kind)

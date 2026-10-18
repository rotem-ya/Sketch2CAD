"""Turn a failed Claude API call into a message the engineer can act on."""
from __future__ import annotations


def explain(exc: BaseException) -> tuple[str, str] | None:
    """(i18n key, detail) for an API error; None when `exc` is not one (the caller shows it as is).

    Most specific first: timeouts before other connection errors, the typed 4xx classes before the
    generic status error.
    """
    import anthropic

    if isinstance(exc, anthropic.APITimeoutError):
        key = "api_timeout"
    elif isinstance(exc, anthropic.APIConnectionError):
        key = "api_offline"
    elif isinstance(exc, anthropic.AuthenticationError):
        key = "api_bad_key"
    elif isinstance(exc, anthropic.PermissionDeniedError):
        key = "api_no_permission"
    elif isinstance(exc, anthropic.RateLimitError):
        key = "api_rate_limit"
    elif isinstance(exc, anthropic.BadRequestError) and "credit balance" in _api_message(exc).lower():
        key = "api_no_credit"
    elif isinstance(exc, anthropic.APIStatusError):
        key = "api_server" if exc.status_code >= 500 else "api_rejected"
    else:
        return None
    detail = _api_message(exc)
    request_id = getattr(exc, "request_id", None)
    if request_id:
        detail = f"{detail} ({request_id})" if detail else request_id
    return key, detail


def _api_message(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return str(exc)

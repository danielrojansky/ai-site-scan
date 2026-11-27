"""Shared PostHog client for Vercel Python functions."""

import atexit
import logging
import os

from posthog import Posthog

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.getenv("VERCEL_ENV") == "production" or os.getenv("ENV") == "production"


def _warn_missing(variable: str) -> None:
    if not _is_production():
        logger.warning(
            "%s variable required by PostHog is missing or un-configured, this causes events to be silently missed. "
            "This error stops appearing once %s is configured",
            variable,
            variable,
        )


def _create_posthog_client() -> Posthog | None:
    project_token = os.getenv("POSTHOG_PROJECT_TOKEN")
    host = os.getenv("POSTHOG_HOST")

    if not project_token:
        _warn_missing("POSTHOG_PROJECT_TOKEN")
        return None
    if not host:
        _warn_missing("POSTHOG_HOST")
        return None

    return Posthog(
        project_token,
        host=host,
        enable_exception_autocapture=True,
    )


posthog_client = _create_posthog_client()


def capture_unhandled_exception(exception: BaseException) -> None:
    """Report an exception caught by a Vercel function's global HTTP boundary."""
    if posthog_client is None:
        return
    try:
        posthog_client.capture_exception(exception)
    except Exception:
        logger.exception("PostHog exception reporting failed")


if posthog_client is not None:
    atexit.register(posthog_client.shutdown)

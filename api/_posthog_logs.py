"""Dedicated PostHog OTLP log export for Vercel Python functions."""

import atexit
import logging
import os

_configuration_logger = logging.getLogger(__name__)
posthog_logs = logging.getLogger("ai_site_scan.posthog_logs")
posthog_logs.addHandler(logging.NullHandler())
posthog_logs.setLevel(logging.INFO)
posthog_logs.propagate = False


def _is_production() -> bool:
    return os.getenv("VERCEL_ENV") == "production" or os.getenv("ENV") == "production"


def _warn_missing(variable: str) -> None:
    if not _is_production():
        _configuration_logger.warning(
            "%s variable required by PostHog is missing or un-configured, this causes events to be silently missed. "
            "This error stops appearing once %s is configured",
            variable,
            variable,
        )


def _configure_posthog_logs() -> None:
    project_token = os.getenv("POSTHOG_PROJECT_TOKEN")
    host = os.getenv("POSTHOG_HOST")
    if not project_token:
        _warn_missing("POSTHOG_PROJECT_TOKEN")
        return
    if not host:
        _warn_missing("POSTHOG_HOST")
        return

    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError:
        _configuration_logger.warning(
            "PostHog log export requires the OpenTelemetry log packages; logs will not be exported"
        )
        return

    logger_provider = LoggerProvider()
    exporter = OTLPLogExporter(
        endpoint=f"{host.rstrip('/')}/i/v1/logs",
        headers={"Authorization": f"Bearer {project_token}"},
    )
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    posthog_logs.addHandler(LoggingHandler(logger_provider=logger_provider))
    atexit.register(logger_provider.shutdown)


_configure_posthog_logs()

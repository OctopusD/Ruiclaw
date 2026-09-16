"""Runtime log visibility controls shared by CLI commands."""

from loguru import logger

__all__ = ["_set_ruiclaw_logs"]


def _set_ruiclaw_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("ruiclaw")
    else:
        logger.disable("ruiclaw")

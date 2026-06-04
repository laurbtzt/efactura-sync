"""Central logging configuration for the efactura-sync CLI.

Configures the ``efactura_sync`` package logger only (not the root logger), so
third-party libraries (httpx, smtplib) are never configured by us. Logs go to
stderr; stdout is reserved for ``typer.echo`` CLI output.
"""

import logging
import sys

_PACKAGE_LOGGER = "efactura_sync"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str) -> None:
    """Configure the ``efactura_sync`` package logger. Idempotent.

    Attaches a single ``StreamHandler(sys.stderr)`` with a human-readable
    formatter and sets the level. A second call updates the level without adding
    a duplicate handler. Unknown level strings fall back to INFO with a warning.

    Propagation is left at its default (enabled). The handler is attached to the
    ``efactura_sync`` logger only — never the root logger — so third-party
    loggers (httpx, smtplib) stay unconfigured. The root logger has no handlers
    in this standalone CLI, so each record is still emitted exactly once.
    """
    logger = logging.getLogger(_PACKAGE_LOGGER)
    numeric = logging.getLevelName(level.upper())
    bad: str | None = None
    if not isinstance(numeric, int):
        numeric = logging.INFO
        bad = level

    logger.setLevel(numeric)

    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        logger.addHandler(handler)
        _configured = True

    if bad is not None:
        logger.warning("unknown log level %r; falling back to INFO", bad)

import logging

import pytest

from efactura_sync.logging_setup import setup_logging

_PKG = "efactura_sync"


def _reset_pkg_logger() -> None:
    logger = logging.getLogger(_PKG)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    # reset the module-level idempotency guard
    import efactura_sync.logging_setup as ls

    ls._configured = False


def test_setup_logging_sets_level_and_one_handler() -> None:
    _reset_pkg_logger()
    setup_logging("DEBUG")
    logger = logging.getLogger(_PKG)
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    # Propagation stays at its default so pytest's caplog (root-based) and any
    # host can observe records; the handler lives only on the package logger.
    assert logger.propagate is True


def test_setup_logging_idempotent_no_duplicate_handler() -> None:
    _reset_pkg_logger()
    setup_logging("DEBUG")
    setup_logging("INFO")
    logger = logging.getLogger(_PKG)
    assert logger.level == logging.INFO
    assert len(logger.handlers) == 1


def test_setup_logging_unknown_level_falls_back_to_info() -> None:
    _reset_pkg_logger()
    setup_logging("garbage")
    logger = logging.getLogger(_PKG)
    assert logger.level == logging.INFO


def test_emitted_record_is_captured_at_level(caplog: pytest.LogCaptureFixture) -> None:
    _reset_pkg_logger()
    setup_logging("INFO")
    with caplog.at_level(logging.INFO, logger="efactura_sync.sample"):
        logging.getLogger("efactura_sync.sample").info("hello %d", 1)
    assert "hello 1" in caplog.text

import pytest

from efactura_sync.errors import (
    AnafApiError,
    AuthError,
    ConfigError,
    EfacturaError,
    InvalidArchiveError,
    PermanentError,
    RefreshTokenExpired,
    RenderError,
    TransientError,
)


def test_efactura_error_is_base() -> None:
    for exc in (AnafApiError, AuthError, ConfigError, RenderError, InvalidArchiveError):
        assert issubclass(exc, EfacturaError)


def test_transient_and_permanent_categorise_anaf_errors() -> None:
    assert issubclass(TransientError, AnafApiError)
    assert issubclass(PermanentError, AnafApiError)


def test_refresh_token_expired_is_auth_error() -> None:
    assert issubclass(RefreshTokenExpired, AuthError)


def test_anaf_api_error_carries_status_and_body() -> None:
    err = AnafApiError("boom", status=503, body=b"upstream down")
    assert err.status == 503
    assert err.body == b"upstream down"
    assert "boom" in str(err)


def test_efactura_error_can_be_raised() -> None:
    with pytest.raises(EfacturaError):
        raise EfacturaError("any")

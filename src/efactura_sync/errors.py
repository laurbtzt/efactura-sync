"""Typed exceptions used across the package."""


class EfacturaError(Exception):
    """Base for all package-specific errors."""


class ConfigError(EfacturaError):
    """Raised when config files are missing, malformed, or invalid."""


class AnafApiError(EfacturaError):
    """Any error from the ANAF HTTP API."""

    def __init__(
        self, message: str, *, status: int | None = None, body: bytes | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class TransientError(AnafApiError):
    """Retriable: network blip, 5xx, 429."""


class PermanentError(AnafApiError):
    """Non-retriable in this run: 4xx (auth, bad request, unknown id)."""


class AuthError(EfacturaError):
    """OAuth / token problems."""


class RefreshTokenExpired(AuthError):
    """The 90-day refresh window has elapsed; user must re-auth on laptop."""


class InvalidArchiveError(EfacturaError):
    """Downloaded ZIP is not a valid archive or is missing expected members."""


class RenderError(EfacturaError):
    """xmltopdf failed (or returned something that isn't a PDF)."""

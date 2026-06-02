"""Load and validate TOML configuration."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from efactura_sync.errors import ConfigError
from efactura_sync.types import Env

TlsMode = Literal["implicit", "starttls"]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    tls: TlsMode
    from_addr: str
    to_addr: str
    error_to_addr: str
    username: str
    password: str


@dataclass(frozen=True)
class AnafConfig:
    default_env: Env
    prod_client_id: str
    prod_client_secret: str
    test_client_id: str
    test_client_secret: str
    prod_redirect_uri: str | None
    test_redirect_uri: str | None


@dataclass(frozen=True)
class Config:
    smtp: SmtpConfig
    anaf: AnafConfig
    log_level: str

    def anaf_credentials(self, env: Env) -> tuple[str, str]:
        if env == "prod":
            return self.anaf.prod_client_id, self.anaf.prod_client_secret
        if env == "test":
            return self.anaf.test_client_id, self.anaf.test_client_secret
        raise ConfigError(f"unknown env: {env!r}")

    def anaf_redirect_uri(self, env: Env) -> str | None:
        if env == "prod":
            return self.anaf.prod_redirect_uri
        if env == "test":
            return self.anaf.test_redirect_uri
        raise ConfigError(f"unknown env: {env!r}")


def _read_toml(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing {label} at {path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {label} ({path}): {e}") from e


def load_config(*, config_path: Path, secrets_path: Path) -> Config:
    cfg = _read_toml(config_path, label="config.toml")
    sec = _read_toml(secrets_path, label="secrets.toml")

    try:
        smtp_cfg = cfg["smtp"]
        anaf_cfg = cfg["anaf"]
        log_cfg = cfg["logging"]
        smtp_sec = sec["smtp"]
        anaf_prod = sec["anaf"]["prod"]
        anaf_test = sec["anaf"]["test"]
    except KeyError as e:
        raise ConfigError(f"missing required section/key: {e}") from e

    tls_raw = smtp_cfg.get("tls", "implicit")
    if tls_raw not in ("implicit", "starttls"):
        raise ConfigError(f"invalid smtp.tls value: {tls_raw!r}")
    tls = cast(TlsMode, tls_raw)

    default_env_raw = anaf_cfg.get("default_env", "prod")
    if default_env_raw not in ("prod", "test"):
        raise ConfigError(f"invalid anaf.default_env: {default_env_raw!r}")
    default_env = cast(Env, default_env_raw)

    port = int(smtp_cfg["port"])
    if not (1 <= port <= 65535):
        raise ConfigError(f"invalid smtp.port: {port} (must be 1..65535)")

    smtp = SmtpConfig(
        host=str(smtp_cfg["host"]),
        port=port,
        tls=tls,
        from_addr=str(smtp_cfg["from_addr"]),
        to_addr=str(smtp_cfg["to_addr"]),
        error_to_addr=str(smtp_cfg.get("error_to_addr", smtp_cfg["to_addr"])),
        username=str(smtp_sec["username"]),
        password=str(smtp_sec["password"]),
    )
    anaf_prod_cfg = anaf_cfg.get("prod", {})
    anaf_test_cfg = anaf_cfg.get("test", {})
    prod_redirect_uri = anaf_prod_cfg.get("redirect_uri")
    test_redirect_uri = anaf_test_cfg.get("redirect_uri")

    anaf = AnafConfig(
        default_env=default_env,
        prod_client_id=str(anaf_prod["client_id"]),
        prod_client_secret=str(anaf_prod["client_secret"]),
        test_client_id=str(anaf_test["client_id"]),
        test_client_secret=str(anaf_test["client_secret"]),
        prod_redirect_uri=str(prod_redirect_uri) if prod_redirect_uri is not None else None,
        test_redirect_uri=str(test_redirect_uri) if test_redirect_uri is not None else None,
    )
    return Config(
        smtp=smtp,
        anaf=anaf,
        log_level=str(log_cfg.get("level", "INFO")),
    )

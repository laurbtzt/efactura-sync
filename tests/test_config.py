from pathlib import Path

import pytest

from efactura_sync.config import Config, load_config, read_log_level
from efactura_sync.errors import ConfigError


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_load_config_minimal(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host = "smtp.example.com"
        port = 465
        tls = "implicit"
        from_addr = "from@example.com"
        to_addr = "to@example.com"

        [anaf]
        default_env = "prod"

        [logging]
        level = "INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username = "u"
        password = "p"

        [anaf.prod]
        client_id = "cid-prod"
        client_secret = "cs-prod"

        [anaf.test]
        client_id = "cid-test"
        client_secret = "cs-test"
        """,
    )

    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)

    assert isinstance(cfg, Config)
    assert cfg.smtp.host == "smtp.example.com"
    assert cfg.smtp.tls == "implicit"
    assert cfg.smtp.username == "u"
    assert cfg.smtp.error_to_addr == "to@example.com"  # defaults to to_addr
    assert cfg.anaf.default_env == "prod"
    assert cfg.anaf_credentials("prod") == ("cid-prod", "cs-prod")
    assert cfg.anaf_credentials("test") == ("cid-test", "cs-test")


def test_load_config_redirect_uri_per_env(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [anaf.prod]
        redirect_uri="https://example.com/cb-prod"
        [anaf.test]
        redirect_uri="https://example.com/cb-test"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)
    assert cfg.anaf_redirect_uri("prod") == "https://example.com/cb-prod"
    assert cfg.anaf_redirect_uri("test") == "https://example.com/cb-test"


def test_load_config_redirect_uri_optional(tmp_path: Path) -> None:
    # The minimal config (no [anaf.prod]/[anaf.test] redirect_uri) yields None.
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)
    assert cfg.anaf_redirect_uri("prod") is None
    assert cfg.anaf_redirect_uri("test") is None


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(config_path=tmp_path / "nope.toml", secrets_path=tmp_path / "secrets.toml")


def test_load_config_invalid_tls(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="weird-mode"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    with pytest.raises(ConfigError, match="tls"):
        load_config(config_path=cfg_path, secrets_path=sec_path)


def test_load_config_invalid_default_env(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="staging"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    with pytest.raises(ConfigError, match="default_env"):
        load_config(config_path=cfg_path, secrets_path=sec_path)


def test_load_config_missing_required_section(tmp_path: Path) -> None:
    # secrets.toml is missing [anaf.test]
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        """,
    )
    with pytest.raises(ConfigError, match="missing"):
        load_config(config_path=cfg_path, secrets_path=sec_path)


def test_load_config_malformed_toml(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, "config.toml", 'host = "unclosed\n')
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(config_path=cfg_path, secrets_path=sec_path)


def test_load_config_invalid_port(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=99999
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    with pytest.raises(ConfigError, match="port"):
        load_config(config_path=cfg_path, secrets_path=sec_path)


def test_anaf_credentials_raises_on_unknown_env(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    sec_path = _write(
        tmp_path,
        "secrets.toml",
        """
        [smtp]
        username="u"
        password="p"
        [anaf.prod]
        client_id="x"
        client_secret="y"
        [anaf.test]
        client_id="x"
        client_secret="y"
        """,
    )
    cfg = load_config(config_path=cfg_path, secrets_path=sec_path)
    # Bypass the type system to simulate a bad call site.
    with pytest.raises(ConfigError, match="unknown env"):
        cfg.anaf_credentials("nope")  # type: ignore[arg-type]


def test_load_config_missing_secrets_file(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        """
        [smtp]
        host="h"
        port=465
        tls="implicit"
        from_addr="a"
        to_addr="b"
        [anaf]
        default_env="prod"
        [logging]
        level="INFO"
        """,
    )
    with pytest.raises(ConfigError, match="secrets.toml"):
        load_config(config_path=cfg_path, secrets_path=tmp_path / "missing-secrets.toml")


def test_read_log_level_returns_configured_value(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        "config.toml",
        """
        [logging]
        level = "DEBUG"
        """,
    )
    assert read_log_level(cfg) == "DEBUG"


def test_read_log_level_missing_file_defaults_info(tmp_path: Path) -> None:
    assert read_log_level(tmp_path / "nope.toml") == "INFO"


def test_read_log_level_no_logging_section_defaults_info(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "config.toml", '[smtp]\nhost = "x"\n')
    assert read_log_level(cfg) == "INFO"


def test_read_log_level_malformed_toml_defaults_info(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "config.toml", "this is = = not toml [[[")
    assert read_log_level(cfg) == "INFO"

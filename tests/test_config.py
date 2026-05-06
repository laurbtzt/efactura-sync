from pathlib import Path

import pytest

from efactura_sync.config import Config, load_config
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
    assert cfg.archive_root.is_absolute()


def test_load_config_archive_root_override(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        "config.toml",
        f"""
        [archive]
        root = "{tmp_path / "arch"}"
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
    assert cfg.archive_root == tmp_path / "arch"


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

from pathlib import Path

import pytest

from src.config import load_config


def _write_config(path: Path, default_method: str = "direct") -> None:
    path.write_text(
        "\n".join(
            [
                "discord:",
                "  guild_id: \"123\"",
                "sources:",
                "  - type: meetup",
                "    name: \"Test\"",
                "    group_slug: \"test-group\"",
                "sync:",
                f"  default_event_creation_method: {default_method}",
            ]
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("method", ["direct", "sesh", "justevent", "JustEvent"])
def test_default_creation_method_is_propagated(tmp_path: Path, method: str):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method=method)

    cfg = load_config(str(cfg_path))
    assert cfg["sources"][0]["event_creation_method"] == method.lower()


def test_env_override_for_default_method(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="direct")

    monkeypatch.setenv("JUSTEVENTS_DEFAULT_EVENT_CREATION_METHOD", "sesh")
    cfg = load_config(str(cfg_path))
    assert cfg["sources"][0]["event_creation_method"] == "sesh"


def test_invalid_method_raises(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="invalid")

    with pytest.raises(ValueError):
        load_config(str(cfg_path))

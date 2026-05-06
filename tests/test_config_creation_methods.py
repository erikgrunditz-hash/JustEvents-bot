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


def _write_config_with_source_method(path: Path, source_method: str, default_method: str = "direct") -> None:
    path.write_text(
        "\n".join(
            [
                "discord:",
                "  guild_id: \"123\"",
                "sources:",
                "  - type: meetup",
                "    name: \"Test\"",
                "    group_slug: \"test-group\"",
                f"    event_creation_method: {source_method}",
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


def test_env_override_for_source_method(tmp_path: Path, monkeypatch):
    """Verify env var overrides source-specific event_creation_method."""
    cfg_path = tmp_path / "config.yaml"
    _write_config_with_source_method(cfg_path, source_method="direct", default_method="justevent")

    # Source explicitly says 'direct', but env var should override it to 'sesh'
    monkeypatch.setenv("JUSTEVENTS_DEFAULT_EVENT_CREATION_METHOD", "sesh")
    cfg = load_config(str(cfg_path))
    assert cfg["sources"][0]["event_creation_method"] == "sesh"


def test_invalid_method_raises(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="invalid")

    with pytest.raises(ValueError):
        load_config(str(cfg_path))


def test_similarity_defaults_are_applied(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="direct")

    cfg = load_config(str(cfg_path))
    similarity = cfg["sync"]["similarity_matching"]
    assert similarity["time_window_minutes"] == 180
    assert similarity["min_title_ratio"] == 0.55
    assert similarity["min_score"] == 0.72


def test_invalid_similarity_time_window_raises(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="direct")

    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\n  similarity_matching:\n"
        + "    time_window_minutes: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(str(cfg_path))


def test_invalid_similarity_ratio_raises(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="direct")

    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\n  similarity_matching:\n"
        + "    min_title_ratio: 1.5\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(str(cfg_path))


def test_exclude_title_contains_defaults_to_empty_list(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="direct")

    cfg = load_config(str(cfg_path))
    assert cfg["sync"]["exclude_title_contains"] == []


def test_invalid_exclude_title_contains_type_raises(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_config(cfg_path, default_method="direct")

    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + "\n  exclude_title_contains: private event\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(str(cfg_path))

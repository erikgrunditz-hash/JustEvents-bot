"""Configuration loader.

Secrets (bot token) are read from environment variables or a ``.env`` file.
Non-secret settings live in ``config/config.yaml``.
"""

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

load_dotenv()

_VALID_CREATION_METHODS = {"direct", "sesh", "justevent"}
_DEFAULT_SIMILARITY_TIME_WINDOW_MINUTES = 180
_DEFAULT_SIMILARITY_MIN_TITLE_RATIO = 0.55
_DEFAULT_SIMILARITY_MIN_SCORE = 0.72


def _normalise_creation_method(value: Any, *, where: str) -> str:
    method = str(value or "direct").strip().lower()
    if method not in _VALID_CREATION_METHODS:
        valid = ", ".join(sorted(_VALID_CREATION_METHODS))
        raise ValueError(f"Invalid event_creation_method at {where}: {value!r}. Expected one of: {valid}")
    return method


def _to_int(value: Any, *, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid integer at {where}: {value!r}")


def _to_float(value: Any, *, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid number at {where}: {value!r}")


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """Load and return the merged configuration dict.

    Environment variables take precedence over values in the YAML file:
    - ``DISCORD_BOT_TOKEN``  → ``discord.bot_token``
    - ``DISCORD_GUILD_ID``   → ``discord.guild_id``
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Copy config/config.example.yaml to config/config.yaml and fill in your settings."
        )

    with path.open(encoding="utf-8") as fh:
        config: Dict[str, Any] = yaml.safe_load(fh) or {}

    discord_cfg = config.setdefault("discord", {})

    # Secrets must come from environment variables (never hard-coded in YAML)
    env_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    env_guild = os.environ.get("DISCORD_GUILD_ID", "")
    if env_token:
        discord_cfg["bot_token"] = env_token
    if env_guild:
        discord_cfg["guild_id"] = env_guild

    # Global default for how events should be created in Discord.
    # Environment variable takes precedence (e.g., workflow input override).
    env_default_creation = os.environ.get("JUSTEVENTS_DEFAULT_EVENT_CREATION_METHOD", "").strip()
    env_override = False
    if env_default_creation:
        default_creation_method = _normalise_creation_method(
            env_default_creation,
            where="env JUSTEVENTS_DEFAULT_EVENT_CREATION_METHOD",
        )
        env_override = True
    else:
        default_creation_method = _normalise_creation_method(
            config.get("sync", {}).get("default_event_creation_method", "direct"),
            where="sync.default_event_creation_method",
        )

    command_cfg = discord_cfg.setdefault("command", {})
    default_command_channel_id = (
        os.environ.get("JUSTEVENTS_COMMAND_CHANNEL_ID", "").strip()
        or str(command_cfg.get("default_channel_id", "")).strip()
    )
    if default_command_channel_id:
        command_cfg["default_channel_id"] = default_command_channel_id

    sync_cfg = config.setdefault("sync", {})
    similarity_cfg = sync_cfg.setdefault("similarity_matching", {})
    time_window_minutes = _to_int(
        similarity_cfg.get("time_window_minutes", _DEFAULT_SIMILARITY_TIME_WINDOW_MINUTES),
        where="sync.similarity_matching.time_window_minutes",
    )
    if time_window_minutes <= 0:
        raise ValueError("sync.similarity_matching.time_window_minutes must be > 0")

    min_title_ratio = _to_float(
        similarity_cfg.get("min_title_ratio", _DEFAULT_SIMILARITY_MIN_TITLE_RATIO),
        where="sync.similarity_matching.min_title_ratio",
    )
    if not 0 <= min_title_ratio <= 1:
        raise ValueError("sync.similarity_matching.min_title_ratio must be between 0 and 1")

    min_score = _to_float(
        similarity_cfg.get("min_score", _DEFAULT_SIMILARITY_MIN_SCORE),
        where="sync.similarity_matching.min_score",
    )
    if not 0 <= min_score <= 1:
        raise ValueError("sync.similarity_matching.min_score must be between 0 and 1")

    similarity_cfg["time_window_minutes"] = time_window_minutes
    similarity_cfg["min_title_ratio"] = min_title_ratio
    similarity_cfg["min_score"] = min_score

    excluded_title_terms = sync_cfg.get("exclude_title_contains", [])
    if excluded_title_terms is None:
        excluded_title_terms = []
    if not isinstance(excluded_title_terms, list):
        raise ValueError("sync.exclude_title_contains must be a list of strings")
    sync_cfg["exclude_title_contains"] = [str(term) for term in excluded_title_terms]

    # Propagate global defaults to each source config that doesn't set them.
    global_lookahead = sync_cfg.get("lookahead_days", 90)
    for source in config.get("sources", []):
        source.setdefault("lookahead_days", global_lookahead)
        # If env var is set, it overrides source config. Otherwise use source config or fall back to default.
        if env_override:
            source["event_creation_method"] = default_creation_method
        else:
            source["event_creation_method"] = _normalise_creation_method(
                source.get("event_creation_method", default_creation_method),
                where=f"sources[{source.get('name', source.get('type', '?'))}].event_creation_method",
            )
        if default_command_channel_id:
            source.setdefault("command_channel_id", default_command_channel_id)

    return config

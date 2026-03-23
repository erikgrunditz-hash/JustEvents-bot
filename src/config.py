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

    # Propagate global lookahead_days to each source config that doesn't set it
    global_lookahead = config.get("sync", {}).get("lookahead_days", 90)
    for source in config.get("sources", []):
        source.setdefault("lookahead_days", global_lookahead)

    return config

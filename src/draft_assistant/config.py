from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "cache"


@dataclass(frozen=True)
class Settings:
    sleeper_league_id: str
    sleeper_draft_id: str
    sleeper_user_id: str
    fantasypros_api_key: str
    fantasypros_season: int
    projections_cache_path: Path
    poll_interval_seconds: float = 4.0
    poll_interval_seconds_near_pick: float = 2.0


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")

    def require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(
                f"Missing required environment variable {name!r}. "
                f"Copy .env.example to .env and fill it in."
            )
        return value

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    return Settings(
        sleeper_league_id=require("SLEEPER_LEAGUE_ID"),
        sleeper_draft_id=require("SLEEPER_DRAFT_ID"),
        sleeper_user_id=require("SLEEPER_USER_ID"),
        fantasypros_api_key=require("FANTASYPROS_API_KEY"),
        fantasypros_season=int(os.environ.get("FANTASYPROS_SEASON", "2026")),
        projections_cache_path=CACHE_DIR / "projections.json",
    )

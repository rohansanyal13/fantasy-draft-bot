from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from draft_assistant.schemas import Position, ProjectionRow, ScoringSettings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.fantasypros.com/public/v2/json"

_POSITIONS_TO_FETCH = (
    Position.QB,
    Position.RB,
    Position.WR,
    Position.TE,
    Position.K,
    Position.DST,
)


def _scoring_format_param(scoring: ScoringSettings) -> str:
    """Maps our internal scoring settings to FantasyPros' scoring-format query value."""
    if scoring.reception_points >= 1.0:
        return "PPR"
    if scoring.reception_points >= 0.5:
        return "HALF"
    return "STD"


class FantasyProsClient:
    """Live FantasyPros projections, with an on-disk cache as fallback.

    NOTE: the exact query parameter names/values and response field names below
    were assembled from FantasyPros' public docs and third-party references, not
    a live authenticated test call — verify against a real response the first time
    this runs (see the ValueError raised in `_extract_rows` if the shape doesn't
    match) and adjust `_extract_rows` accordingly.
    """

    def __init__(self, api_key: str, cache_path: Path, http: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._cache_path = cache_path
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=15.0)
        self._owns_client = http is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def get_season_projections(
        self, season: int, scoring: ScoringSettings
    ) -> list[ProjectionRow]:
        try:
            rows = await self._fetch_live(season, scoring)
            self._write_cache(rows)
            return rows
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("FantasyPros live fetch failed (%s); falling back to cache.", exc)
            cached = self._read_cache()
            if cached is not None:
                return cached
            raise RuntimeError(
                "FantasyPros API unreachable and no cached projections on disk — "
                "cannot build the player pool. Check connectivity/API key, or seed "
                f"{self._cache_path} manually."
            ) from exc

    async def _fetch_live(self, season: int, scoring: ScoringSettings) -> list[ProjectionRow]:
        rows: list[ProjectionRow] = []
        scoring_param = _scoring_format_param(scoring)
        for position in _POSITIONS_TO_FETCH:
            resp = await self._http.get(
                f"/nfl/{season}/projections",
                headers={"x-api-key": self._api_key},
                params={"position": position.value, "week": "draft", "scoring": scoring_param},
            )
            resp.raise_for_status()
            rows.extend(self._extract_rows(resp.json(), position))
        return rows

    @staticmethod
    def _extract_rows(payload: dict, position: Position) -> list[ProjectionRow]:
        players = payload.get("players")
        if players is None:
            raise ValueError(
                f"Unexpected FantasyPros response shape (no 'players' key): {list(payload.keys())!r}"
            )
        rows = []
        for p in players:
            name = p.get("player_name") or p.get("name")
            team = p.get("player_team_id") or p.get("team")
            points = p.get("fpts") or p.get("stats", {}).get("fpts") if isinstance(p.get("stats"), dict) else None
            if name is None or points is None:
                raise ValueError(
                    f"Unexpected FantasyPros player record shape, could not find name/points: {p!r}"
                )
            rows.append(
                ProjectionRow(
                    name=name,
                    team=team or "FA",
                    position=position,
                    projected_points=float(points),
                )
            )
        return rows

    def _write_cache(self, rows: list[ProjectionRow]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps([r.model_dump() for r in rows], indent=2))

    def _read_cache(self) -> list[ProjectionRow] | None:
        if not self._cache_path.exists():
            return None
        raw = json.loads(self._cache_path.read_text())
        return [ProjectionRow.model_validate(r) for r in raw]

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from draft_assistant.schemas import PlayerRecord, Position, ProjectionRow

logger = logging.getLogger(__name__)

_NAME_MATCH_THRESHOLD = 85.0


@dataclass
class ReconciliationResult:
    players: list[PlayerRecord]
    unmatched: list[ProjectionRow] = field(default_factory=list)


def _normalize_name(name: str) -> str:
    suffixes = (" jr.", " jr", " sr.", " sr", " ii", " iii", " iv")
    lowered = name.lower().replace(".", "").replace("'", "")
    for suffix in suffixes:
        if lowered.endswith(suffix):
            lowered = lowered[: -len(suffix)]
    return lowered.strip()


def reconcile_projections(
    sleeper_catalog: dict[str, dict],
    projections: list[ProjectionRow],
    manual_overrides: dict[str, str] | None = None,
) -> ReconciliationResult:
    """Joins FantasyPros ProjectionRows to Sleeper player_ids.

    Strategy: exact match on (normalized name, position) first; falls back to
    fuzzy name matching within the same position when there's no exact hit.
    `manual_overrides` maps a FantasyPros player name -> a Sleeper player_id,
    for the residual mismatches fuzzy matching can't resolve (defense/team
    units, rookies not yet in one catalog, unusual suffixes, etc).
    """
    overrides = manual_overrides or {}

    by_position: dict[Position, dict[str, str]] = {}  # position -> {normalized_name: player_id}
    for player_id, raw in sleeper_catalog.items():
        pos = raw.get("position")
        if pos not in Position.__members__:
            continue
        full_name = raw.get("full_name") or f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip()
        if not full_name:
            continue
        by_position.setdefault(Position(pos), {})[_normalize_name(full_name)] = player_id

    players: list[PlayerRecord] = []
    unmatched: list[ProjectionRow] = []

    for proj in projections:
        if proj.name in overrides:
            player_id = overrides[proj.name]
        else:
            candidates = by_position.get(proj.position, {})
            normalized = _normalize_name(proj.name)
            player_id = candidates.get(normalized)
            if player_id is None and candidates:
                best = process.extractOne(
                    normalized, candidates.keys(), scorer=fuzz.token_sort_ratio
                )
                if best is not None and best[1] >= _NAME_MATCH_THRESHOLD:
                    player_id = candidates[best[0]]

        if player_id is None:
            unmatched.append(proj)
            logger.warning("No Sleeper player_id match for %r (%s)", proj.name, proj.position.value)
            continue

        raw = sleeper_catalog[player_id]
        players.append(
            PlayerRecord(
                player_id=player_id,
                full_name=proj.name,
                team=proj.team,
                position=proj.position,
                projected_points=proj.projected_points,
            )
        )

    return ReconciliationResult(players=players, unmatched=unmatched)

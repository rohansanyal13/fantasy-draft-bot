from __future__ import annotations

import logging

from draft_assistant.config import Settings
from draft_assistant.fantasypros_client import FantasyProsClient
from draft_assistant.reconciliation import reconcile_projections
from draft_assistant.sleeper_client import SleeperClient
from draft_assistant.state import DraftPool

logger = logging.getLogger(__name__)


async def bootstrap(settings: Settings) -> DraftPool:
    """Cold-start: fetch league/draft metadata, the player catalog, and
    projections; reconcile IDs; build the initial DraftPool; replay any picks
    already made (covers both a fresh start after the draft began and a
    restart after a crash — Sleeper's picks endpoint is the durable source of
    truth, so recovery is just re-deriving state from it rather than
    maintaining a separate local snapshot).
    """
    sleeper = SleeperClient()
    fantasypros = FantasyProsClient(settings.fantasypros_api_key, settings.projections_cache_path)
    try:
        scoring = await sleeper.get_league_scoring(settings.sleeper_league_id)
        requirements = await sleeper.get_roster_requirements(settings.sleeper_league_id)
        slot_assignments = await sleeper.get_draft_slot_assignments(settings.sleeper_draft_id)
        catalog = await sleeper.get_players_catalog()
        projections = await fantasypros.get_season_projections(settings.fantasypros_season, scoring)
        existing_picks = await sleeper.get_picks(settings.sleeper_draft_id)
    finally:
        await sleeper.aclose()
        await fantasypros.aclose()

    for assignment in slot_assignments:
        assignment.is_self = assignment.user_id == settings.sleeper_user_id
    if not any(a.is_self for a in slot_assignments):
        raise RuntimeError(
            f"SLEEPER_USER_ID {settings.sleeper_user_id!r} was not found among this "
            f"draft's {len(slot_assignments)} participants — check the .env value."
        )

    result = reconcile_projections(catalog, projections)
    if result.unmatched:
        logger.warning(
            "%d projection(s) had no Sleeper player_id match and were dropped: %s",
            len(result.unmatched),
            ", ".join(row.name for row in result.unmatched),
        )

    pool = DraftPool(players=result.players, requirements=requirements, slot_assignments=slot_assignments)
    for pick in existing_picks:
        pool.apply_pick(pick)

    logger.info(
        "Bootstrap complete: %d players in pool, %d already drafted, self roster_id=%d",
        len(pool.players_by_id),
        len(pool.drafted_ids),
        pool.self_roster_id,
    )
    return pool

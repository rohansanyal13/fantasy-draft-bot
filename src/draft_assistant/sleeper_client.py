from __future__ import annotations

import httpx

from draft_assistant.schemas import (
    DraftSlotAssignment,
    PickEvent,
    Position,
    RosterRequirements,
    ScoringSettings,
)

BASE_URL = "https://api.sleeper.app/v1"

# Sleeper's scoring-settings keys -> our ScoringSettings fields.
_SCORING_KEY_MAP: dict[str, str] = {
    "rec": "reception_points",
    "pass_td": "passing_td_points",
    "pass_yd": "passing_yard_points",
    "pass_int": "passing_int_points",
    "rush_td": "rushing_td_points",
    "rush_yd": "rushing_yard_points",
    "rec_td": "receiving_td_points",
    "rec_yd": "receiving_yard_points",
    "fum_lost": "fumble_lost_points",
}

# Sleeper roster "position" slot codes -> our Position enum (ignores BN/IR/taxi slots).
_STARTER_SLOT_MAP: dict[str, Position] = {
    "QB": Position.QB,
    "RB": Position.RB,
    "WR": Position.WR,
    "TE": Position.TE,
    "K": Position.K,
    "DEF": Position.DST,
}
_FLEX_SLOT_NAMES = {"FLEX", "SUPER_FLEX", "REC_FLEX", "WRRB_FLEX"}


class SleeperClient:
    """Thin async client over the public, unauthenticated Sleeper API."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)
        self._owns_client = http is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def get_league_scoring(self, league_id: str) -> ScoringSettings:
        resp = await self._http.get(f"/league/{league_id}")
        resp.raise_for_status()
        raw = resp.json()["scoring_settings"]
        kwargs = {
            field: raw[key] for key, field in _SCORING_KEY_MAP.items() if key in raw
        }
        return ScoringSettings(**kwargs)

    async def get_roster_requirements(self, league_id: str) -> RosterRequirements:
        resp = await self._http.get(f"/league/{league_id}")
        resp.raise_for_status()
        roster_positions: list[str] = resp.json()["roster_positions"]

        starters: dict[Position, int] = {}
        flex_slots = 0
        bench_slots = 0
        for slot in roster_positions:
            if slot in _FLEX_SLOT_NAMES:
                flex_slots += 1
            elif slot in ("BN", "IR", "TAXI"):
                bench_slots += 1
            elif slot in _STARTER_SLOT_MAP:
                pos = _STARTER_SLOT_MAP[slot]
                starters[pos] = starters.get(pos, 0) + 1
        return RosterRequirements(starters=starters, flex_slots=flex_slots, bench_slots=bench_slots)

    async def get_draft_slot_assignments(self, draft_id: str) -> list[DraftSlotAssignment]:
        """Combines the draft's `draft_order` (user_id -> slot) and `slot_to_roster_id`
        (slot -> roster_id) maps into one assignment per drafter."""
        resp = await self._http.get(f"/draft/{draft_id}")
        resp.raise_for_status()
        raw = resp.json()
        draft_order: dict[str, int] = raw.get("draft_order") or {}
        slot_to_roster_id: dict[str, int] = raw.get("slot_to_roster_id") or {}

        assignments = []
        for user_id, slot in draft_order.items():
            roster_id = slot_to_roster_id.get(str(slot))
            if roster_id is None:
                continue
            assignments.append(
                DraftSlotAssignment(roster_id=int(roster_id), user_id=str(user_id), draft_slot=int(slot))
            )
        return sorted(assignments, key=lambda a: a.draft_slot)

    async def get_players_catalog(self) -> dict[str, dict]:
        """Full NFL player catalog, keyed by Sleeper player_id.

        Large (multi-MB) payload — fetch once at bootstrap and cache to disk,
        never call this from inside the draft loop.
        """
        resp = await self._http.get("/players/nfl")
        resp.raise_for_status()
        return resp.json()

    async def get_picks(self, draft_id: str) -> list[PickEvent]:
        resp = await self._http.get(f"/draft/{draft_id}/picks")
        resp.raise_for_status()
        raw_picks = resp.json()
        return [
            PickEvent(
                pick_no=p["pick_no"],
                round=p["round"],
                draft_slot=p["draft_slot"],
                roster_id=p["roster_id"],
                player_id=p["player_id"],
                picked_at=p.get("picked_at") or 0,
            )
            for p in raw_picks
            if p.get("player_id")
        ]

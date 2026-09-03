from __future__ import annotations

import threading
from dataclasses import dataclass, field

from draft_assistant import valuation
from draft_assistant.schemas import (
    FLEX_ELIGIBLE,
    DraftSlotAssignment,
    PickEvent,
    PlayerRecord,
    RosterRequirements,
    RosterState,
)

TOP_N_AVAILABLE = 50
SURVIVAL_CANDIDATE_COUNT = 15
LOOKAHEAD_ROUNDS = 3


def pick_slot_for(pick_no: int, num_teams: int) -> int:
    """1-indexed draft_slot on the clock for a given overall pick number, snake order."""
    round_no = (pick_no - 1) // num_teams + 1
    pos_in_round = (pick_no - 1) % num_teams
    return pos_in_round + 1 if round_no % 2 == 1 else num_teams - pos_in_round


class DraftPool:
    """Single source of truth for drafted status, rosters, and derived valuation.

    Owned exclusively by the compute worker thread — not thread-safe on its own.
    The UI never touches this directly; it only ever reads a `DraftView` snapshot
    handed off through `SharedState`.
    """

    def __init__(
        self,
        players: list[PlayerRecord],
        requirements: RosterRequirements,
        slot_assignments: list[DraftSlotAssignment],
    ) -> None:
        self.requirements = requirements
        self.slot_assignments = sorted(slot_assignments, key=lambda a: a.draft_slot)
        self.players_by_id: dict[str, PlayerRecord] = {p.player_id: p for p in players}
        self.drafted_ids: set[str] = set()
        self.roster_states: dict[int, RosterState] = {
            a.roster_id: RosterState(roster_id=a.roster_id, is_self=a.is_self)
            for a in self.slot_assignments
        }
        self.pick_history: list[PickEvent] = []
        self.num_teams = len(self.slot_assignments)
        self._slot_to_roster_id = {a.draft_slot: a.roster_id for a in self.slot_assignments}

        self._recompute_valuation()

    @property
    def self_roster_id(self) -> int:
        for a in self.slot_assignments:
            if a.is_self:
                return a.roster_id
        raise RuntimeError("No draft slot assignment marked is_self")

    def available_players(self) -> list[PlayerRecord]:
        return [p for p in self.players_by_id.values() if not p.is_drafted]

    def apply_pick(self, pick: PickEvent) -> bool:
        """Idempotent: returns False (no-op) if this player_id was already recorded."""
        if pick.player_id in self.drafted_ids:
            return False
        player = self.players_by_id.get(pick.player_id)
        if player is None:
            return False  # unmatched player_id (reconciliation gap) — skip rather than crash

        player.is_drafted = True
        player.drafted_by_roster_id = pick.roster_id
        self.drafted_ids.add(pick.player_id)
        self.pick_history.append(pick)

        roster = self.roster_states.setdefault(pick.roster_id, RosterState(roster_id=pick.roster_id))
        self._assign_to_roster(roster, player)
        self._recompute_valuation()
        return True

    def _assign_to_roster(self, roster: RosterState, player: PlayerRecord) -> None:
        open_slots = roster.open_starter_slots(self.requirements)
        if open_slots.get(player.position, 0) > 0:
            roster.starters_filled[player.position] = roster.starters_filled.get(player.position, 0) + 1
        elif player.position in FLEX_ELIGIBLE and roster.flex_filled < self.requirements.flex_slots:
            roster.flex_filled += 1
        else:
            roster.bench.append(player.player_id)
        roster.drafted_player_ids.append(player.player_id)

    def _recompute_valuation(self) -> None:
        """Full recompute over the current pool. Scoped to the affected position's
        slice by construction (compute_replacement_baselines only iterates the
        relevant position pools), so this stays well within the sub-50ms budget
        described in the architecture doc without needing manual dirty-tracking.
        """
        all_players = list(self.players_by_id.values())
        baselines = valuation.compute_replacement_baselines(all_players, self.requirements, self.num_teams)
        valuation.apply_vorp(all_players, baselines)
        user_roster = self.roster_states[self.self_roster_id]
        valuation.apply_need(all_players, user_roster, self.requirements, self.players_by_id)

    def picks_until_next_turn(self, current_pick_no: int) -> int:
        self_slot = next(a.draft_slot for a in self.slot_assignments if a.is_self)
        limit = current_pick_no + self.num_teams * LOOKAHEAD_ROUNDS
        for pick_no in range(current_pick_no, limit + 1):
            if pick_slot_for(pick_no, self.num_teams) == self_slot:
                return pick_no - current_pick_no
        return self.num_teams * LOOKAHEAD_ROUNDS

    def opponents_for_next_k_picks(self, current_pick_no: int, k: int) -> list[RosterState]:
        opponents = []
        for offset in range(k):
            slot = pick_slot_for(current_pick_no + offset, self.num_teams)
            roster_id = self._slot_to_roster_id.get(slot)
            if roster_id is not None and roster_id in self.roster_states:
                opponents.append(self.roster_states[roster_id])
        return opponents


@dataclass
class DraftView:
    """Read-only rendered snapshot for the presentation layer."""

    available_players: list[PlayerRecord]
    user_roster: RosterState
    opponent_rosters: list[RosterState] = field(default_factory=list)
    picks_made: int = 0
    picks_until_next_turn: int = 0
    last_error: str | None = None


def build_view(pool: DraftPool, current_pick_no: int, top_n: int = TOP_N_AVAILABLE) -> DraftView:
    available = sorted(pool.available_players(), key=lambda p: p.score, reverse=True)[:top_n]

    k = pool.picks_until_next_turn(current_pick_no)
    opponents_between = pool.opponents_for_next_k_picks(current_pick_no, k)
    run_rate = valuation.positional_run_rate(pool.pick_history, pool.players_by_id)

    for player in available[:SURVIVAL_CANDIDATE_COUNT]:
        viable_targets = sum(1 for p in pool.available_players() if p.position == player.position)
        player.survival_probability = valuation.survival_probability(
            player.position, opponents_between, pool.requirements, run_rate, viable_targets
        )

    return DraftView(
        available_players=available,
        user_roster=pool.roster_states[pool.self_roster_id],
        opponent_rosters=[r for rid, r in pool.roster_states.items() if rid != pool.self_roster_id],
        picks_made=len(pool.drafted_ids),
        picks_until_next_turn=k,
    )


class SharedState:
    """Thread-safe hand-off between the compute worker thread and the Streamlit
    UI's rerun cycle — see architecture doc section 1.3."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._view: DraftView | None = None

    def set_view(self, view: DraftView) -> None:
        with self._lock:
            self._view = view

    def get_view(self) -> DraftView | None:
        with self._lock:
            return self._view

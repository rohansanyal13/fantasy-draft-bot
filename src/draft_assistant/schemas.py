from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Position(str, Enum):
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    K = "K"
    DST = "DST"


FLEX_ELIGIBLE = frozenset({Position.RB, Position.WR, Position.TE})


class ScoringSettings(BaseModel):
    """Derived from GET /v1/league/{league_id}."""

    reception_points: float = 0.0
    passing_td_points: float = 4.0
    passing_yard_points: float = 0.04
    passing_int_points: float = -2.0
    rushing_td_points: float = 6.0
    rushing_yard_points: float = 0.1
    receiving_td_points: float = 6.0
    receiving_yard_points: float = 0.1
    fumble_lost_points: float = -2.0


class RosterRequirements(BaseModel):
    """Starting-slot counts, derived from league settings."""

    starters: dict[Position, int] = Field(default_factory=dict)
    flex_slots: int = 0
    bench_slots: int = 0

    def total_starter_slots(self, position: Position) -> int:
        return self.starters.get(position, 0)


class PlayerRecord(BaseModel):
    """One row in the available/drafted player pool."""

    player_id: str
    full_name: str
    team: str
    position: Position
    tier: int | None = None

    projected_points: float = 0.0
    replacement_points: float = 0.0
    vorp: float = 0.0
    need_multiplier: float = 1.0
    score: float = 0.0
    survival_probability: float | None = None

    is_drafted: bool = False
    drafted_by_roster_id: int | None = None


class DraftSlotAssignment(BaseModel):
    """Maps a Sleeper roster/user to its position in snake order."""

    roster_id: int
    user_id: str
    draft_slot: int
    is_self: bool = False


class RosterState(BaseModel):
    """One team's roster as of the current point in the draft."""

    roster_id: int
    is_self: bool = False
    starters_filled: dict[Position, int] = Field(default_factory=dict)
    flex_filled: int = 0
    bench: list[str] = Field(default_factory=list)
    drafted_player_ids: list[str] = Field(default_factory=list)

    def open_starter_slots(self, requirements: RosterRequirements) -> dict[Position, int]:
        return {
            pos: max(0, requirements.starters.get(pos, 0) - self.starters_filled.get(pos, 0))
            for pos in requirements.starters
        }

    def bench_count_at(self, position: Position, players_by_id: dict[str, PlayerRecord]) -> int:
        return sum(1 for pid in self.bench if players_by_id[pid].position == position)


class PickEvent(BaseModel):
    """One entry from GET /v1/draft/{draft_id}/picks."""

    pick_no: int
    round: int
    draft_slot: int
    roster_id: int
    player_id: str
    picked_at: int = 0


class ProjectionRow(BaseModel):
    """Normalized shape both the live FantasyPros response and the disk cache map into."""

    name: str
    team: str
    position: Position
    projected_points: float

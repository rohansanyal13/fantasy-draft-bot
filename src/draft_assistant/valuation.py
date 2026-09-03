from __future__ import annotations

from collections import Counter

from draft_assistant.schemas import (
    FLEX_ELIGIBLE,
    PickEvent,
    PlayerRecord,
    Position,
    RosterRequirements,
    RosterState,
)

BENCH_DECAY = 0.75
RUN_WINDOW = 5
DEFAULT_RUN_RATE = 1.0 / 6.0


# ---------------------------------------------------------------------------
# 3.1 Dynamic VORP
# ---------------------------------------------------------------------------


def compute_replacement_baselines(
    all_players: list[PlayerRecord],
    requirements: RosterRequirements,
    num_teams: int,
) -> dict[Position, float]:
    """Replacement_p(t) for every position, per architecture doc section 3.1.

    Non-flex positions (QB/K/DST) get an independent baseline. RB/WR/TE share
    one blended baseline derived from the combined flex-eligible pool, since
    they compete for the same FLEX slot.
    """
    baselines: dict[Position, float] = {}

    for position in (Position.QB, Position.K, Position.DST):
        pool = sorted(
            (p for p in all_players if p.position == position),
            key=lambda p: p.projected_points,
            reverse=True,
        )
        base_rank = num_teams * requirements.total_starter_slots(position)
        drafted = sum(1 for p in pool if p.is_drafted)
        baselines[position] = _rank_value(pool, base_rank, drafted)

    flex_pool = sorted(
        (p for p in all_players if p.position in FLEX_ELIGIBLE),
        key=lambda p: p.projected_points,
        reverse=True,
    )
    flex_base_rank = num_teams * (
        sum(requirements.total_starter_slots(pos) for pos in FLEX_ELIGIBLE)
        + requirements.flex_slots
    )
    flex_drafted = sum(1 for p in flex_pool if p.is_drafted)
    flex_replacement = _rank_value(flex_pool, flex_base_rank, flex_drafted)
    for position in FLEX_ELIGIBLE:
        baselines[position] = flex_replacement

    return baselines


def _rank_value(pool_sorted_desc: list[PlayerRecord], base_rank: int, drafted_count: int) -> float:
    if not pool_sorted_desc:
        return 0.0
    rank = base_rank + drafted_count  # 1-indexed
    idx = min(max(rank, 1), len(pool_sorted_desc)) - 1
    return pool_sorted_desc[idx].projected_points


def apply_vorp(players: list[PlayerRecord], baselines: dict[Position, float]) -> None:
    """Mutates each player's `replacement_points` and `vorp` in place."""
    for player in players:
        replacement = baselines.get(player.position, 0.0)
        player.replacement_points = replacement
        player.vorp = player.projected_points - replacement


# ---------------------------------------------------------------------------
# 3.2 Positional Need / Marginal Utility Penalty
# ---------------------------------------------------------------------------


def need_multiplier(
    position: Position,
    roster: RosterState,
    requirements: RosterRequirements,
    players_by_id: dict[str, PlayerRecord],
    decay: float = BENCH_DECAY,
) -> float:
    open_slots = roster.open_starter_slots(requirements)
    if open_slots.get(position, 0) > 0:
        return 1.0
    if position in FLEX_ELIGIBLE and roster.flex_filled < requirements.flex_slots:
        return 1.0
    bench_count = roster.bench_count_at(position, players_by_id)
    return decay**bench_count


def apply_need(
    players: list[PlayerRecord],
    user_roster: RosterState,
    requirements: RosterRequirements,
    players_by_id: dict[str, PlayerRecord],
) -> None:
    """Mutates each player's `need_multiplier` and `score` in place, relative
    to the user's own roster."""
    for player in players:
        player.need_multiplier = need_multiplier(player.position, user_roster, requirements, players_by_id)
        player.score = player.vorp * player.need_multiplier


# ---------------------------------------------------------------------------
# 3.3 Draft-Run & Drop-off Survival Model
# ---------------------------------------------------------------------------


def positional_run_rate(
    recent_picks: list[PickEvent],
    players_by_id: dict[str, PlayerRecord],
    window: int = RUN_WINDOW,
) -> dict[Position, float]:
    recent = recent_picks[-window:]
    if not recent:
        return {}
    positions = [
        players_by_id[p.player_id].position for p in recent if p.player_id in players_by_id
    ]
    if not positions:
        return {}
    counts = Counter(positions)
    return {pos: count / len(positions) for pos, count in counts.items()}


def _opponent_pick_probability(
    position: Position,
    opponent: RosterState,
    requirements: RosterRequirements,
    run_rate: dict[Position, float],
    viable_targets: int,
) -> float:
    if viable_targets <= 0:
        return 0.0
    open_slots = opponent.open_starter_slots(requirements)
    if open_slots.get(position, 0) > 0:
        need_weight = 1.0
    elif position in FLEX_ELIGIBLE and opponent.flex_filled < requirements.flex_slots:
        need_weight = 0.5
    else:
        need_weight = 0.1
    rate = run_rate.get(position, DEFAULT_RUN_RATE)
    return min(max(need_weight * rate / viable_targets, 0.0), 1.0)


def survival_probability(
    position: Position,
    opponents_between: list[RosterState],
    requirements: RosterRequirements,
    run_rate: dict[Position, float],
    viable_targets: int,
) -> float:
    """P(survive) = prod(1 - p_i) over the k intervening opponent picks."""
    prob_survive = 1.0
    for opponent in opponents_between:
        p_i = _opponent_pick_probability(position, opponent, requirements, run_rate, viable_targets)
        prob_survive *= 1.0 - p_i
    return prob_survive

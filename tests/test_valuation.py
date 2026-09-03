from draft_assistant.schemas import PlayerRecord, Position, RosterRequirements, RosterState
from draft_assistant.valuation import (
    apply_need,
    apply_vorp,
    compute_replacement_baselines,
    need_multiplier,
    survival_probability,
)


def make_player(player_id: str, position: Position, points: float, drafted: bool = False) -> PlayerRecord:
    return PlayerRecord(
        player_id=player_id,
        full_name=player_id,
        team="XX",
        position=position,
        projected_points=points,
        is_drafted=drafted,
    )


def make_requirements() -> RosterRequirements:
    return RosterRequirements(
        starters={Position.QB: 1, Position.RB: 2, Position.WR: 2, Position.TE: 1},
        flex_slots=1,
        bench_slots=6,
    )


def test_qb_replacement_baseline_uses_static_rank_when_none_drafted():
    players = [make_player(f"qb{i}", Position.QB, 300 - i * 10) for i in range(20)]
    requirements = make_requirements()
    baselines = compute_replacement_baselines(players, requirements, num_teams=10)
    # 10 teams * 1 starter = rank 10 -> the 10th-best QB (0-indexed 9) projects at 300-90=210
    assert baselines[Position.QB] == 210


def test_qb_replacement_baseline_shifts_after_players_drafted():
    players = [make_player(f"qb{i}", Position.QB, 300 - i * 10) for i in range(20)]
    requirements = make_requirements()
    for p in players[:5]:
        p.is_drafted = True
    baselines = compute_replacement_baselines(players, requirements, num_teams=10)
    # rank shifts from 10 to 10+5=15 -> 0-indexed 14 -> 300-140=160
    assert baselines[Position.QB] == 160


def test_flex_eligible_positions_share_one_blended_baseline():
    rbs = [make_player(f"rb{i}", Position.RB, 250 - i * 5) for i in range(15)]
    wrs = [make_player(f"wr{i}", Position.WR, 240 - i * 5) for i in range(15)]
    tes = [make_player(f"te{i}", Position.TE, 150 - i * 5) for i in range(15)]
    requirements = make_requirements()
    baselines = compute_replacement_baselines(rbs + wrs + tes, requirements, num_teams=10)
    assert baselines[Position.RB] == baselines[Position.WR] == baselines[Position.TE]


def test_vorp_is_projected_minus_replacement():
    players = [make_player(f"qb{i}", Position.QB, 300 - i * 10) for i in range(20)]
    requirements = make_requirements()
    baselines = compute_replacement_baselines(players, requirements, num_teams=10)
    apply_vorp(players, baselines)
    assert players[0].vorp == players[0].projected_points - baselines[Position.QB]


def test_need_multiplier_full_when_starting_slot_open():
    roster = RosterState(roster_id=1)
    requirements = make_requirements()
    assert need_multiplier(Position.QB, roster, requirements, {}) == 1.0


def test_need_multiplier_decays_once_starters_and_flex_full():
    roster = RosterState(
        roster_id=1,
        starters_filled={Position.RB: 2},
        flex_filled=1,
        bench=["rb_bench_1"],
    )
    requirements = make_requirements()
    players_by_id = {"rb_bench_1": make_player("rb_bench_1", Position.RB, 100)}
    multiplier = need_multiplier(Position.RB, roster, requirements, players_by_id)
    assert multiplier == 0.75  # BENCH_DECAY ** 1 bench copy


def test_apply_need_produces_lower_score_for_full_position():
    requirements = make_requirements()
    players_by_id: dict[str, PlayerRecord] = {}
    open_roster = RosterState(roster_id=1)
    full_roster = RosterState(
        roster_id=2, starters_filled={Position.RB: 2}, flex_filled=1, bench=["rb_bench"]
    )
    players_by_id["rb_bench"] = make_player("rb_bench", Position.RB, 90, drafted=True)

    candidate = make_player("rb_x", Position.RB, 200)
    candidate.vorp = 50.0
    players_by_id[candidate.player_id] = candidate

    apply_need([candidate], open_roster, requirements, players_by_id)
    open_score = candidate.score

    apply_need([candidate], full_roster, requirements, players_by_id)
    full_score = candidate.score

    assert full_score < open_score


def test_survival_probability_decreases_with_more_intervening_picks():
    requirements = make_requirements()
    run_rate = {Position.RB: 0.4}
    needy_opponent = RosterState(roster_id=2)  # open RB slot -> high pick probability

    survive_1 = survival_probability(Position.RB, [needy_opponent], requirements, run_rate, viable_targets=5)
    survive_3 = survival_probability(
        Position.RB, [needy_opponent] * 3, requirements, run_rate, viable_targets=5
    )
    assert 0.0 <= survive_3 < survive_1 <= 1.0


def test_survival_probability_is_one_with_no_intervening_picks():
    requirements = make_requirements()
    assert survival_probability(Position.RB, [], requirements, {}, viable_targets=5) == 1.0

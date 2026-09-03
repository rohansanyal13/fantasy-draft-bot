from draft_assistant.schemas import DraftSlotAssignment, PickEvent, PlayerRecord, Position, RosterRequirements
from draft_assistant.state import DraftPool, pick_slot_for


def test_pick_slot_for_snakes_between_rounds():
    num_teams = 4
    # Round 1: 1,2,3,4 ; Round 2 (snake): 4,3,2,1 ; Round 3: 1,2,3,4
    assert [pick_slot_for(n, num_teams) for n in range(1, 13)] == [
        1, 2, 3, 4,
        4, 3, 2, 1,
        1, 2, 3, 4,
    ]


def make_pool() -> DraftPool:
    players = [
        PlayerRecord(player_id="p1", full_name="P One", team="XX", position=Position.RB, projected_points=200),
        PlayerRecord(player_id="p2", full_name="P Two", team="XX", position=Position.WR, projected_points=180),
    ]
    requirements = RosterRequirements(
        starters={Position.RB: 2, Position.WR: 2}, flex_slots=1, bench_slots=4
    )
    slots = [
        DraftSlotAssignment(roster_id=1, user_id="me", draft_slot=1, is_self=True),
        DraftSlotAssignment(roster_id=2, user_id="them", draft_slot=2, is_self=False),
    ]
    return DraftPool(players=players, requirements=requirements, slot_assignments=slots)


def test_apply_pick_marks_player_drafted_and_updates_roster():
    pool = make_pool()
    applied = pool.apply_pick(PickEvent(pick_no=1, round=1, draft_slot=1, roster_id=1, player_id="p1"))
    assert applied is True
    assert "p1" in pool.drafted_ids
    assert pool.players_by_id["p1"].is_drafted is True
    assert pool.roster_states[1].starters_filled[Position.RB] == 1


def test_apply_pick_is_idempotent_on_duplicate_delivery():
    pool = make_pool()
    pool.apply_pick(PickEvent(pick_no=1, round=1, draft_slot=1, roster_id=1, player_id="p1"))
    applied_again = pool.apply_pick(PickEvent(pick_no=1, round=1, draft_slot=1, roster_id=1, player_id="p1"))
    assert applied_again is False
    assert len(pool.roster_states[1].drafted_player_ids) == 1


def test_picks_until_next_turn_for_self_at_slot_one_of_two():
    pool = make_pool()  # self is draft_slot 1, other team is slot 2
    # currently on the clock at pick 1 (self's own turn) -> 0 picks until next turn... but
    # semantics here are "next" turn, so from pick 1 itself the next self turn is pick 4 (snake)
    assert pool.picks_until_next_turn(2) == 2  # pick 2 is opponent; self is next at pick 4 -> gap of 2

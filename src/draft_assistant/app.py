from __future__ import annotations

import asyncio
import logging

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from draft_assistant.bootstrap import bootstrap
from draft_assistant.config import load_settings
from draft_assistant.draft_loop import DraftLoopRunner
from draft_assistant.schemas import PlayerRecord, RosterState
from draft_assistant.state import SharedState

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Fantasy Draft Assistant", layout="wide")


@st.cache_resource
def start_draft_loop() -> DraftLoopRunner:
    """Runs once per process (Streamlit caches the result across reruns and
    sessions) — this is what prevents a second poller/compute thread pair
    from spinning up on every script rerun. See architecture doc section 1.3.
    """
    settings = load_settings()
    pool = asyncio.run(bootstrap(settings))
    runner = DraftLoopRunner(pool, settings, SharedState())
    runner.start()
    return runner


def _players_dataframe(players: list[PlayerRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Player": p.full_name,
                "Pos": p.position.value,
                "Team": p.team,
                "Tier": p.tier,
                "Proj Pts": round(p.projected_points, 1),
                "VORP": round(p.vorp, 1),
                "Score": round(p.score, 1),
                "Survives to next pick": (
                    f"{p.survival_probability:.0%}" if p.survival_probability is not None else "—"
                ),
            }
            for p in players
        ]
    )


def _roster_summary(roster: RosterState) -> pd.DataFrame:
    rows = [{"Position": pos.value, "Filled": count} for pos, count in roster.starters_filled.items()]
    rows.append({"Position": "FLEX", "Filled": roster.flex_filled})
    rows.append({"Position": "Bench", "Filled": len(roster.bench)})
    return pd.DataFrame(rows)


def main() -> None:
    st.title("Fantasy Draft Assistant")

    try:
        runner = start_draft_loop()
    except Exception as exc:  # bootstrap/config failure — surface clearly, don't crash the page
        st.error(f"Failed to start: {exc}")
        st.stop()
        return

    st_autorefresh(interval=2000, key="autorefresh")

    view = runner.shared_state.get_view()
    if view is None:
        st.info("Waiting for the first valuation pass...")
        st.stop()
        return

    st.metric("Picks made", view.picks_made)
    st.metric("Your pick in", f"{view.picks_until_next_turn} picks")

    col_available, col_rosters = st.columns([3, 1])

    with col_available:
        st.subheader("Available players")
        st.dataframe(_players_dataframe(view.available_players), use_container_width=True, hide_index=True)

    with col_rosters:
        st.subheader("Your roster")
        st.dataframe(_roster_summary(view.user_roster), use_container_width=True, hide_index=True)

        st.subheader("Opponents")
        for opp in view.opponent_rosters:
            with st.expander(f"Roster {opp.roster_id}"):
                st.dataframe(_roster_summary(opp), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import logging
import queue
import threading

from draft_assistant.config import Settings
from draft_assistant.schemas import PickEvent
from draft_assistant.sleeper_client import SleeperClient
from draft_assistant.state import DraftPool, SharedState, build_view

logger = logging.getLogger(__name__)

NEAR_PICK_THRESHOLD = 3  # tighten poll interval once this close to the user's turn
MAX_BACKOFF_SECONDS = 10.0


class DraftLoopRunner:
    """Owns the background I/O poller thread and compute worker thread — see
    architecture doc sections 1.2/1.3. Start once per process; a Streamlit
    script rerun must not spawn a second copy (guarded via st.session_state
    in app.py).
    """

    def __init__(self, pool: DraftPool, settings: Settings, shared_state: SharedState) -> None:
        self._pool = pool
        self._settings = settings
        self._shared_state = shared_state
        self._pick_queue: queue.Queue[list[PickEvent]] = queue.Queue()
        self._stop_event = threading.Event()

        current_pick_no = len(pool.drafted_ids) + 1
        self._shared_state.set_view(build_view(pool, current_pick_no))

    @property
    def shared_state(self) -> SharedState:
        return self._shared_state

    def start(self) -> None:
        threading.Thread(target=self._run_io_loop, name="draft-poller", daemon=True).start()
        threading.Thread(target=self._run_compute_loop, name="draft-compute", daemon=True).start()

    def stop(self) -> None:
        self._stop_event.set()

    # -- I/O thread: async polling loop, decoupled from compute -----------

    def _run_io_loop(self) -> None:
        asyncio.run(self._poll_forever())

    async def _poll_forever(self) -> None:
        client = SleeperClient()
        consecutive_failures = 0
        try:
            while not self._stop_event.is_set():
                try:
                    picks = await client.get_picks(self._settings.sleeper_draft_id)
                    new_picks = [p for p in picks if p.player_id not in self._pool.drafted_ids]
                    if new_picks:
                        self._pick_queue.put(new_picks)
                    consecutive_failures = 0
                    interval = self._poll_interval()
                except Exception:
                    logger.exception("Poll tick failed; backing off before retry.")
                    consecutive_failures += 1
                    interval = min(2.0**consecutive_failures, MAX_BACKOFF_SECONDS)
                await asyncio.sleep(interval)
        finally:
            await client.aclose()

    def _poll_interval(self) -> float:
        view = self._shared_state.get_view()
        if view is not None and view.picks_until_next_turn <= NEAR_PICK_THRESHOLD:
            return self._settings.poll_interval_seconds_near_pick
        return self._settings.poll_interval_seconds

    # -- Compute thread: applies picks, recomputes valuation, publishes ---

    def _run_compute_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                new_picks = self._pick_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            for pick in new_picks:
                self._pool.apply_pick(pick)  # idempotent — safe even on duplicate/out-of-order delivery

            current_pick_no = len(self._pool.drafted_ids) + 1
            view = build_view(self._pool, current_pick_no)
            self._shared_state.set_view(view)

# Fantasy Draft Assistant

A real-time draft assistant for a live Sleeper fantasy football snake draft. Runs locally as a Streamlit dashboard, ranking available players by a dynamically recomputed value score (VORP, adjusted for your roster's positional need) and flagging how likely each one is to survive to your next pick.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design — component topology, data schemas, the valuation math, and the trade-offs behind each decision.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
```

Copy `.env.example` to `.env` and fill in:

| Variable | How to find it |
|---|---|
| `SLEEPER_LEAGUE_ID` | In your league's Sleeper URL: `sleeper.app/leagues/<league_id>/...` |
| `SLEEPER_DRAFT_ID` | In your draft's Sleeper URL: `sleeper.app/draft/nfl/<draft_id>` |
| `SLEEPER_USER_ID` | `GET https://api.sleeper.app/v1/user/<your_username>` — no auth needed, response includes `user_id` |
| `FANTASYPROS_API_KEY` | From your FantasyPros account's API settings |
| `FANTASYPROS_SEASON` | e.g. `2026` |

`.env` is gitignored — never commit it.

Note: `SLEEPER_USER_ID` must appear in the draft's slot assignments, which Sleeper only populates once the commissioner finalizes/randomizes draft order (usually shortly before the draft starts). Running this before then will fail bootstrap with a clear error.

## Run

```powershell
uv run streamlit run src/draft_assistant/app.py
```

Opens at `http://localhost:8501`. Keep it open in a browser tab next to the Sleeper draft room.

## Test

```powershell
uv run pytest
```

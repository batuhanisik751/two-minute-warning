# Two-Minute Warning

**Two-Minute Warning** is an open, point-in-time NFL early-warning app. It reads every play of every NFL game since 1999 and flags what is about to change: players about to break into fantasy lineups, hot streaks that won't last, coaches whose decisions are costing their teams wins, coaches about to be fired, and players about to break out, or fall off a cliff, next season.

Every flag comes with a **track record**. Each model is tested the honest way: trained only on seasons before the one it predicts, using only data that was available at the moment of the prediction. A **time machine** lets you pick any week since 2012 and see exactly what the app would have said then, and what actually happened.

Built on the open-source [nflverse](https://nflverse.nflverse.com/) data ecosystem. Unofficial, educational, not affiliated with the NFL or ESPN, and not betting advice.

## Setup

```bash
uv sync --all-extras
cp .env.example .env   # fill in locally; never commit
uv run twm --help
```

See `PROJECT_SPEC.md` for the full specification and `docs/progress.md` for the build log.

## Attribution

Data from nflverse; rankings and player ID maps from DynastyProcess / FantasyPros; snap counts originate from Pro Football Reference.

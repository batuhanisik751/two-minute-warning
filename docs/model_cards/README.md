# Model cards

Source: `PROJECT_SPEC.md`, "Model cards per module"
One card per production model pinned in `config/production_models.yaml` (PROJECT_SPEC,
Prototype 3: "Model cards per module"). Every card has the same headings: Purpose and intended
use; Inputs and point-in-time rules; Model and training; Evaluation; Calibration; Known limits
and failure cases; Owner decisions that shaped it; Reproducibility; Ethical notes.

**How the numbers are sourced.** No number in a card is typed from memory. Each block of a card
starts with a line `Source: `<file>`` (optionally with quoted section names), and every number
in that block is copied verbatim from that file: the Evaluation and Calibration sections cite
the generated reports under `reports/`, the pin facts cite `config/production_models.yaml`, and
the rest cite the module docs. `tests/test_model_cards.py` checks, offline, that every pin has a
card naming its pin id and approved date, that every card has the same headings, and that every
number in every card appears as the same token in the file it cites (no rounding).

Source: `config/production_models.yaml`

| Card | Pin key | Pin id (model_version) | Kind | Approved |
|---|---|---|---|---|
| [Waiver Radar](waiver_radar.md) | `waiver_radar` | `logit-5828a082c9e09093` | logistic regression | 2026-09-28 |
| [K streamer](streamer_k.md) | `streamer_k` | `logit_k-dd21dd427fddf989` | logistic regression | 2026-09-30 |
| [D/ST streamer](streamer_dst.md) | `streamer_dst` | `baseline_opponent_dst-e76f31d80f3a905e` | a rule, not a model | 2026-10-01 |
| [Regression Watch](regression_watch.md) | `regression_watch` | `zero_flat_all-9d98d6f251ee054c` (xFP `own_xfp-2026-8577dfffe4683678`) | frozen parameters + own walk-forward xFP models | 2026-10-01 |
| [Decision Report Card](decisions_wp.md) | `decisions` | `grading-100604cddaabf068` | grading spec: WP model, sub-models, rules | 2026-10-01 |
| [Hot-Seat Meter](hot_seat.md) | `hot_seat` | `logit-0ede038915531ea3` | L2 logistic regression | 2026-10-02 |
| [Cliff board](board_cliff.md) | `board` | `board_spec-1581a6da7602c3f7` | spec: Cliff + missed-time logistic regressions (Breakout: research note) | 2026-10-02 |
| [Questionable outcomes](questionable.md) | `questionable` | `lookup-5f0566085a6b1f24` | lookup table: counted rates, shrunk toward parent cells | 2026-10-05 |
| [Start/sit odds](startsit.md) (local only) | `startsit` | `rank_dist-61ea917e5a85b44a` | spec: rank -> points distributions (FantasyPros weekly ranks; never published) | 2026-10-05 |
| [Teammate out](teammate_out.md) | `teammate_out` | `alloc-a9b857bb4f1b6a00` | allocation table: counted share changes, shrunk toward the position group (the owner overrode the pre-set rule's "nothing changes") | 2026-10-05 |

**Reproducing any card's model:** `uv run twm model check <module>` (the pin, every file's
sha256 and the reports it must reproduce) and `uv run twm timemachine verify --module <module>`
(recomputes the stored lists from the warehouse; see `docs/timemachine.md`).

**When a pin changes:** update the card in the same commit as the pin and the reports; the test
fails if a card names an old pin id or a number the new report no longer contains.

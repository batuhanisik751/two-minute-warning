"""Plain-English reasons for the weekly Waiver Radar list (C6, PROJECT_SPEC 8.1 and 11 C6:
"top feature contributions -> plain-English templates from the registry").

**Contributions (logistic regression).** The model's log-odds for a player is
``intercept + sum_j coef_j * z_j`` over the transformed columns ``z`` (imputed, scaled, with
a missing-value indicator per numeric feature and one yes/no column per position). Measured
from the TRAINING MEAN ``m_j`` of every column, the log-odds is

    baseline + sum_j coef_j * (z_j - m_j),   baseline = intercept + sum_j coef_j * m_j,

so each column's term ``coef_j * (z_j - m_j)`` is how much it moves this player away from an
average training row. A feature's **contribution** is the sum of its columns' terms (its
value, its missing indicator, and for ``position`` its one-hot columns). The contributions add
up exactly to the log-odds minus the baseline (tested). Positive = pushes his chance up.

**Choosing the reasons** (:func:`explain`): the features with the largest positive
contributions, in order, at most :data:`N_REASONS`, with four rules so every line is a useful,
true sentence for a beginner:

1. at most one reason per **theme** (:data:`THEMES`: snaps, targets, carries, expected
   points, scoring, depth chart, teammates ...): three ways of saying "he plays a lot" are one
   reason, not three;
2. :data:`NEVER_REASONS` are never reasons: ``position`` moves a whole position list alike,
   and the calendar features (``team_games_remaining``, ``n_opp_games_seen``) are about the
   week, not the player: none of them can explain a ranking within a week's list;
3. the value itself must push the chance up, both from the average training row and from
   the average player of his list this week (his position's pool at this as-of): a feature
   whose positive contribution comes only from its missing-value indicator (the value is known
   at all, e.g. a birth date on file) would give the wrong reason ("is 30 years old: younger
   players ..."), and one whose value is no better than the average player of his list this
   week does not explain why he ranks above them;
4. a feature is skipped when its sentence would not be true in plain words: a missing value
   (no data is not a reason), a "rise" that is not a rise (the model can like a snap-share
   change of -2 points because it is above the training average), a "soft schedule" of
   opponents who allow less than the average, a "no" with no sentence.

Each reason is phrased with the feature's registry sentence (``twm.registry``,
``reason_template`` / ``reason_if_false``) filled from the player's own feature row at the
as-of, and the unavailable teammates of that row (``teammates_out``: who, and why he is out).
A feature without a sentence falls back to ``"<title>: <value>"`` (:func:`fallback_features`
lists them). Every fact comes from the same point-in-time row the model scored, so a reason
can never describe anything after the as-of (the scoring function, reasons included, passes
the leakage harness).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from twm import registry as rg
from twm.modules.waiver_radar.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS
from twm.modules.waiver_radar.models import _base_feature

N_REASONS = 3
WINDOW = 3  # the "last 3 games" window of the features
NEVER_REASONS = frozenset({"position", "team_games_remaining", "n_opp_games_seen"})
CONTRIBUTION_DECIMALS = 6

THEMES: dict[str, str] = {
    **dict.fromkeys(("snap_share_last", "snap_share_avg3", "snap_share_season",
                     "snap_share_delta", "routes_proxy_avg3"), "snaps"),
    **dict.fromkeys(("target_share_last", "target_share_avg3", "air_yards_share_avg3",
                     "wopr_avg3", "rz_targets_avg3"), "targets"),
    **dict.fromkeys(("carry_share_last", "carry_share_avg3", "rz_carries_avg3",
                     "gl_opps_avg3"), "carries"),
    **dict.fromkeys(("xfp_last", "xfp_avg3", "fpoe_avg3"), "expected points"),
    **dict.fromkeys(("fantasy_points_last", "fantasy_points_avg3", "ppg_to_date",
                     "ppg_pos_rank", "games_played_to_date"), "scoring"),
    **dict.fromkeys(("preseason_pos_rank", "preseason_ranked"), "preseason"),
    **dict.fromkeys(("depth_rank_now", "depth_rank_prev", "depth_rank_change",
                     "depth_listed"), "depth chart"),
    **dict.fromkeys(("vacated_target_share", "vacated_carry_share",
                     "same_pos_vacated_target_share", "same_pos_vacated_carry_share",
                     "teammate_same_pos_unavailable", "top_teammate_out"), "teammates"),
    "joined_team_recently": "new team",
    **dict.fromkeys(("team_epa_per_play", "team_epa_per_play_neutral", "team_plays_per_game",
                     "team_pass_rate_neutral"), "team offense"),
    **dict.fromkeys(("opp_fp_allowed_next3", "n_opp_games_seen", "bye_in_next3",
                     "team_games_remaining"), "schedule"),
    **dict.fromkeys(("position", "age_at_asof", "is_rookie", "draft_round", "is_undrafted",
                     "years_exp"), "player"),
}  # fmt: skip

# Why a teammate is unavailable, from the rule that fired first (features.UNAVAILABILITY_RULES)
# and the roster / injury-report status: (present tense, after "after").
ROSTER_STATUS_WHY: dict[str, tuple[str, str]] = {
    "RES": ("is on a reserve list (such as injured reserve)",
            "went on a reserve list (such as injured reserve)"),
    "CUT": ("was released", "was released"),
    "RET": ("retired", "retired"),
}  # fmt: skip
INJURY_WHY: dict[str, tuple[str, str]] = {
    "Out": ("was ruled out of the last game", "was ruled out of the last game"),
    "Doubtful": ("was listed as doubtful for the last game",
                 "was listed as doubtful for the last game"),
}  # fmt: skip
LEFT_TEAM_WHY = (
    "is no longer on the team's roster (released or traded)",
    "left the team's roster (released or traded)",
)
MISSED_WHY = ("missed the last game", "missed the last game")
TEAMMATE_CLAUSE = " after {teammate} {why}"  # appended to the snap-share rise
# Sentences that state a direction: only used when the value, as printed, goes that way.
PREMISES = {
    "fpoe_avg3": lambda v: round(v, 1) > 0,  # "more than his chances were worth"
    "opp_fp_allowed_next3": lambda v: round(v, 2) > 1.0,  # "soft schedule"
    "team_epa_per_play": lambda v: round(v, 2) > 0,  # "efficient"
    "team_epa_per_play_neutral": lambda v: round(v, 2) > 0,
}


# --------------------------------------------------------------------------------------
# Contributions
# --------------------------------------------------------------------------------------


@dataclass
class Parts:
    """A logistic regression's contributions, one column per feature, one row per player."""

    baseline: float  # log-odds of an average training row: intercept + coef . training means
    total: pl.DataFrame  # contributions; baseline + row sum = the model's log-odds
    value: pl.DataFrame  # the same without the missing indicators (the value's own push)
    peer: pl.DataFrame | None  # value part measured from the mean of the player's list


def contribution_parts(model: Any, x: pl.DataFrame, groups: Sequence[Any] | None = None) -> Parts:
    """The contributions of every feature for the rows of ``x`` (module docstring) for a
    :class:`~twm.modules.waiver_radar.production.ProductionModel` logistic regression.
    ``groups`` (one label per row, e.g. the position): also measure each value part from the
    mean of the row's group instead of the training mean (``Parts.peer``)."""
    clf = model.classifier
    coef = np.asarray(clf.coef_[0], dtype=np.float64)
    means = np.asarray(model.train_means, dtype=np.float64)
    z = model.transform(x)
    terms = (z - means) * coef
    baseline = float(clf.intercept_[0] + coef @ means)
    total: dict[str, list[int]] = {f: [] for f in model.features}
    value: dict[str, list[int]] = {f: [] for f in model.features}
    for i, name in enumerate(model.transformed_names()):
        f = _base_feature(name, model.features)
        total[f].append(i)
        if not name.split("__", 1)[1].startswith("missingindicator_"):
            value[f].append(i)

    def frame(t: np.ndarray, g: dict[str, list[int]]) -> pl.DataFrame:
        cols = {f: t[:, idx].sum(axis=1) if idx else np.zeros(x.height) for f, idx in g.items()}
        return pl.DataFrame(cols, schema=dict.fromkeys(model.features, pl.Float64))

    peer = None
    if groups is not None:
        labels = np.asarray(list(groups), dtype=object)
        centred = np.empty_like(z)
        for g in dict.fromkeys(labels.tolist()):
            sel = labels == g
            centred[sel] = z[sel] - z[sel].mean(axis=0)
        peer = frame(centred * coef, value)
    return Parts(baseline, frame(terms, total), frame(terms, value), peer)


def contributions(model: Any, x: pl.DataFrame) -> tuple[float, pl.DataFrame]:
    """(baseline, contributions) of :func:`contribution_parts`."""
    parts = contribution_parts(model, x)
    return parts.baseline, parts.total


def log_odds(model: Any, x: pl.DataFrame) -> np.ndarray:
    """The classifier's log-odds (decision function) of the rows."""
    return np.asarray(model.classifier.decision_function(model.transform(x)), dtype=np.float64)


# --------------------------------------------------------------------------------------
# Teammates
# --------------------------------------------------------------------------------------


def teammate_why(mate: Mapping[str, Any], *, after: bool = False) -> str:
    """Why an unavailable teammate is out, in words (``after``: the form that follows
    "after", e.g. "went on a reserve list")."""
    i = 1 if after else 0
    rule = mate.get("rule")
    if rule == "roster_status":
        status = mate.get("status")
        if status in ROSTER_STATUS_WHY:
            return ROSTER_STATUS_WHY[status][i]
        return (
            f"left the active roster ({status})" if after else f"is listed {status} on the roster"
        )
    if rule == "left_team":
        return LEFT_TEAM_WHY[i]
    if rule == "injury_report":
        return INJURY_WHY.get(mate.get("report_status") or "Out", INJURY_WHY["Out"])[i]
    return MISSED_WHY[i]


def parse_teammates(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        return []
    return [m for m in out if isinstance(m, dict)]


def _named(mates: list[dict[str, Any]], share: str | None) -> tuple[str, str] | None:
    """(name text, why) of the teammate with the biggest ``share`` (snap share when None);
    ``and N more`` when several are out."""
    if not mates:
        return None
    key = share or "snap_share_avg3"
    top = sorted(mates, key=lambda m: (-(m.get(key) or 0.0), m.get("gsis_id") or ""))[0]
    name = top.get("name") or "a teammate"
    why = teammate_why(top)
    extra = len(mates) - 1
    if extra:
        why += f" (and {extra} more teammate{'s are' if extra > 1 else ' is'} out)"
    return name, why


# --------------------------------------------------------------------------------------
# Sentences
# --------------------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def fallback_text(feature: str, value: Any) -> str:
    """``"<title>: <value>"`` for a feature without a sentence."""
    e = rg.get(feature)
    if isinstance(value, bool):
        shown = "yes" if value else "no"
    elif "share" in e.unit and _num(value) is not None:
        shown = f"{_num(value):.0%}"
    elif _num(value) is not None:
        v = _num(value)
        assert v is not None
        shown = f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}"
    else:
        shown = str(value)
    return f"{e.title}: {shown}"


def phrase(feature: str, row: Mapping[str, Any], *, weeks: int | None = None) -> str | None:
    """The sentence for ``feature`` from the player's feature row (plus ``team``, ``position``,
    ``preseason_rank_pos`` and ``teammates_out``), or None when it would not be a true, useful
    sentence (see the module docstring, rule 3)."""
    value = row.get(feature)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    e = rg.get(feature)
    pos = row.get("position") or ""
    fields: dict[str, Any] = {
        "player": row.get("name") or "He",
        "value": value,
        "prev": None,
        "delta": None,
        "weeks": weeks if weeks is not None else WINDOW,
        "teammate": "",
        "team": row.get("team") or "his team",
        "why": "",
        "pos": pos,
    }
    mates = parse_teammates(row.get("teammates_out"))
    same = [m for m in mates if m.get("position") == pos]
    template = e.reason_template
    if isinstance(value, bool):
        template = e.reason_template if value else e.reason_if_false
        if template is None:
            return None
    v = _num(value)
    if feature == "snap_share_delta":
        last = _num(row.get("snap_share_last"))
        if last is None or v is None or round((last - v) * 100) >= round(last * 100):
            return None  # not a visible rise
        fields.update(value=last, prev=last - v, delta=v)
    elif feature == "depth_rank_change":
        now, prev = _num(row.get("depth_rank_now")), _num(row.get("depth_rank_prev"))
        if v is None or v <= 0 or now is None or prev is None:
            return None
        fields.update(value=now, prev=prev, delta=v)
    elif feature in PREMISES and (v is None or not PREMISES[feature](v)):
        return None  # "more than", "soft", "efficient" would not be true as shown
    elif feature == "preseason_pos_rank":
        fields["pos"] = row.get("preseason_rank_pos") or pos
    elif feature in ("vacated_target_share", "vacated_carry_share"):
        share = "target_share_avg3" if "target" in feature else "carry_share_avg3"
        who = _named([m for m in mates if (m.get(share) or 0) > 0], share)
        if v is None or round(v, 2) <= 0 or who is None:
            return None
        fields.update(teammate=who[0], why=who[1])
    elif feature in ("same_pos_vacated_target_share", "same_pos_vacated_carry_share"):
        share = "target_share_avg3" if "target" in feature else "carry_share_avg3"
        who = _named([m for m in same if (m.get(share) or 0) > 0], share)
        if v is None or round(v, 2) <= 0 or who is None:
            return None
        fields.update(teammate=who[0], why=who[1])
    elif feature == "teammate_same_pos_unavailable":
        if v is None or v < 1 or not same:
            return None
        fields["teammate"] = ", ".join(m.get("name") or "?" for m in same)
    elif feature == "top_teammate_out":
        if value is not True:
            return None
        ahead = [m for m in same if m.get("ahead")] or same
        who = _named(ahead, None)
        if who is None:
            return None
        fields.update(teammate=who[0], why=who[1])
    if template is None:
        return fallback_text(feature, value)
    text = template.format(**fields)
    if feature == "snap_share_delta" and row.get("top_teammate_out") is True:
        ahead = [m for m in same if m.get("ahead")]
        if ahead:
            top = sorted(ahead, key=lambda m: (-(m.get("snap_share_avg3") or 0.0),
                                               m.get("gsis_id") or ""))[0]  # fmt: skip
            text += TEAMMATE_CLAUSE.format(
                teammate=top.get("name") or "a teammate", why=teammate_why(top, after=True)
            )
    return text


def explain_row(
    row: Mapping[str, Any],
    contrib: Mapping[str, float],
    *,
    value_part: Mapping[str, float] | None = None,
    peer_part: Mapping[str, float] | None = None,
    weeks: int | None = None,
    n: int = N_REASONS,
    features: Sequence[str] = FEATURE_COLUMNS,
) -> list[dict[str, Any]]:
    """Up to ``n`` reasons for one player: [{feature, theme, contribution, text}], strongest
    first (module docstring). ``value_part`` / ``peer_part``: each feature's contribution
    without its missing indicator, from the training mean / from the mean of his list (rule 3;
    None = not checked)."""
    order = sorted(features, key=lambda f: (-contrib.get(f, 0.0), features.index(f)))
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for f in order:
        c = float(contrib.get(f, 0.0))
        if c <= 0 or len(out) >= n:
            break
        theme = THEMES.get(f, f)
        if f in NEVER_REASONS or theme in used:
            continue
        if value_part is not None and float(value_part.get(f, c)) <= 0:
            continue  # only "the value is known" pushes it up: not a reason in words
        if peer_part is not None and float(peer_part.get(f, c)) <= 0:
            continue  # no better than the other players of his list: not why he ranks high
        text = phrase(f, row, weeks=weeks)
        if text is None:
            continue
        used.add(theme)
        out.append(
            {"feature": f, "theme": theme, "contribution": round(c, CONTRIBUTION_DECIMALS),
             "text": text}
        )  # fmt: skip
    return out


def explain(
    model: Any,
    rows: pl.DataFrame,
    *,
    weeks: Sequence[int | None] | None = None,
    n: int = N_REASONS,
) -> tuple[list[list[dict[str, Any]]], pl.DataFrame, float]:
    """Reasons for every row of ``rows`` (feature rows with ``name``, ``team``, ``position``,
    ``teammates_out`` ...; one week's lists): (reasons per row, the contributions frame, the
    baseline). The players of a list are the rows sharing a ``position``."""
    groups = rows.get_column("position").to_list() if "position" in rows.columns else None
    parts = contribution_parts(model, rows, groups)
    feats = list(model.features)
    peers = parts.peer.iter_rows(named=True) if parts.peer is not None else None
    out = []
    for i, (row, c, v) in enumerate(
        zip(
            rows.iter_rows(named=True),
            parts.total.iter_rows(named=True),
            parts.value.iter_rows(named=True),
            strict=True,
        )
    ):
        w = None if weeks is None else weeks[i]
        pp = next(peers) if peers is not None else None
        out.append(explain_row(row, c, value_part=v, peer_part=pp, weeks=w, n=n, features=feats))
    return out, parts.total, parts.baseline


def reasons_json(reasons: list[dict[str, Any]]) -> str:
    return json.dumps(reasons, sort_keys=True, separators=(",", ":"))


def fallback_features(features: Sequence[str] = FEATURE_COLUMNS) -> list[str]:
    """Features whose reason is the generic "<title>: <value>" (no registry sentence), not
    counting those that are never reasons."""
    return [
        f
        for f in features
        if f not in NEVER_REASONS
        and f not in CATEGORICAL_FEATURES
        and rg.get(f).reason_template is None
    ]

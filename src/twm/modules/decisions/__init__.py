"""The Decision Report Card (PROJECT_SPEC 8.4, P2). G1: the app's own win-probability model,
trained walk-forward: ``wp_data`` (one row per scrimmage play state from the typed warehouse,
with the label and every filter counted), ``wp`` (the LightGBM model with monotone constraints,
the fold runner on the shared harness, save/load, and :func:`wp.wp` for any real or
hypothetical state), ``wp_report`` (reports/decisions/wp_backtest.md + .csv against nflfastR's
``wp`` and ``vegas_wp``) and ``cli`` (``twm decisions wp-backtest``); walk-through in
notebooks/03_wp_model.ipynb. G2, the fourth-down sub-models: ``submodels`` (shared loader,
estimator, fold runner, hypothetical states), ``conversion`` (P(convert) and where the ball ends
up), ``fieldgoal`` (P(make), the miss spot), ``punt`` (result distribution, expected WP),
``tries`` (extra-point and two-point rates) and ``submodels_report``
(``twm decisions submodels-backtest``; definitions in docs/decision_metrics.md). G3-G6 (grades,
pages) build on them.
"""

"""The Decision Report Card (PROJECT_SPEC 8.4, P2). G1: the app's own win-probability model,
trained walk-forward: ``wp_data`` (one row per scrimmage play state from the typed warehouse,
with the label and every filter counted), ``wp`` (the LightGBM model with monotone constraints,
the fold runner on the shared harness, save/load, and :func:`wp.wp` for any real or
hypothetical state), ``wp_report`` (reports/decisions/wp_backtest.md + .csv against nflfastR's
``wp`` and ``vegas_wp``) and ``cli`` (``twm decisions wp-backtest``); walk-through in
notebooks/03_wp_model.ipynb. G2-G6 (conversion / FG / punt models, grades, pages) build on it.
"""

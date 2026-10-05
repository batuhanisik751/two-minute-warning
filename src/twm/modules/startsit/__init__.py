"""Start/sit odds (feature #2): "should I start A or B this week?" -> "A outscores B about 62%
of the time", from how players at each FantasyPros weekly expert rank actually scored.

LOCAL ONLY. The input is FantasyPros' weekly expert consensus ranks, whose terms forbid
republishing them (web/lib/third-party.ts, docs/deploy.md step 10). Nothing here is imported by
the publish layer or the pipeline (tests/test_startsit_local_only.py), nothing goes to Postgres
or web/. The frozen artifacts hold only rank -> points distributions and aggregate backtest
numbers derived from public results, never a player's rank (docs/start_sit.md).
"""

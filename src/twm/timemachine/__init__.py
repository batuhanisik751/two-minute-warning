"""The Time Machine's reproducibility check (step I3a; PROJECT_SPEC P3 acceptance: "Time Machine
for any backtest week reproduces the stored predictions exactly (no recomputation drift)").

``twm timemachine verify`` picks past seasons of every module deterministically (seeded; the
first and last backtest season always), recomputes those lists from the warehouse with the
model that was used then (the walk-forward fold of that season, refit with the backtest's own
code, or the stored fold models where the module keeps them) and compares them with the rows
the site serves (the frozen pin snapshot; the Radar's also with the predictions store): same
lists, same entities, same order and rank, probabilities within :data:`compare.TOLERANCE`, same
reasons / drivers / versions. :mod:`.compare` holds the comparison, :mod:`.recompute` the
modules' recomputations; docs/timemachine.md says what is reproduced and what cannot be.
"""

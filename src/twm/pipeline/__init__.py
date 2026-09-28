"""The scheduled pipeline (step E4): `twm pipeline run`, run by .github/workflows/pipeline.yml.

- :mod:`twm.pipeline.schedule`: in season or not, which week's list is due, which attempt is
  the last one (pure functions of the clock, the config and the season's schedule);
- :mod:`twm.pipeline.runner`: the stages in order, with clear exit codes and logs;
- :mod:`twm.pipeline.report`: the job summary, the run's files and the retrain report.
"""

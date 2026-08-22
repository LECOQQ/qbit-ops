# Probes

A probe measures **why** something is wrong. A test keeps it **fixed**.
The two are different artefacts and only one of them belongs in CI.

    probe   ->  finding  ->  structural test  ->  probe kept as a script

`rate_sampler_cadence.py` is the case that produced the rule. The
Overview's graph stuttered, and three causes were plausible: timer
drift, render cost, or stamping a sample at the reply instead of at the
tick. Measured over three 22-second scenarios:

    timer drift        p50 1.00s, max 1.03s          ruled out
    render cost        p50 2.6ms out of 1000ms       ruled out
    stamped at reply   spacing 0.05s -> 1.94s        confirmed

The fix -- open the column on the tick, settle it when the reply lands
-- is held by an ordinary unit test that runs in milliseconds. **The
probe is not that test**: it sleeps for 66 seconds and measures wall
clock, so putting it in the suite would buy flakiness and no coverage.

It stays here because the next time the cadence looks wrong, re-running
it beats re-deriving it:

    poetry run python scripts/probes/rate_sampler_cadence.py

Probes are diagnostics, so they may sleep, use wall clock, and print
tables. They must never be imported by `src/` or by the suite -- but
they are tracked code, so they pass `ruff` and `black` like everything
else here. A probe that cannot be read is a probe nobody re-runs.

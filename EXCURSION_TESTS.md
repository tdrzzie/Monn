# MAE/MFE forward exit hypotheses

`exness_arena/excursion_tests.py` is an independent, order-free research engine. The dashboard Research page shows the full strategy catalog, profile readiness and matched forward comparisons. Profiles are instrument-, source- and parameter-context specific, including observation cohort. Original strategy source identities are retained; unrelated research/dashboard edits are excluded from the signal fingerprint. No live exit, sizing, promotion, loss record, ownership or halt is changed.

## Frozen calibration

Only completed original (`proposed`) shadow observations in `live_evaluation` or prospective `test_only` cohorts can calibrate. Startup seeds are excluded. Each label's recorded close-observation event, not its backdated modeled bar exit, must precede the profile cutoff. Minimum support is 30 completed observations, two UTC entry dates and five gross winners within one context. Profiles freeze once; new data cannot retune an existing profile. Training IDs, data checksum, source context, selection rule, cutoff and method identity are recorded privately.

Three fixed hypotheses compare with original protection: (1) stop at the 80th percentile of winning absolute MAE, clipped to 0.25–1.0 original risk-distance; (2) target at the 60th percentile of all-trade MFE, clipped to 0.25–3.0 original risk-distance; (3) both. Calibration is exploratory and uses gross, censored excursions, not a claim of net-optimal exits. Wider stops are not inferred from paths cut off by existing stops. Strategies lacking support, signals or compatible single-leg protective proposals remain gated. Controllers and unsupported multi-leg flows do not acquire invented independent trades.

## Forward replay

Only signals observed after profile freeze and after research deployment are enrolled. Calibration trades are never reused as test results. Four paired variants receive the same provider bid/ask path. Entry is the first quote strictly after 250 ms; a rejected first fill is not retried. Stops and targets remain anchored to the source proposal. Broker stop distances, lot grids, minimum lots, symbol lot caps, a 10% reserve and the 3.50 USC position ceiling remain enforced. Volume cannot exceed the source shadow volume. Tightening a stop never grants additional live risk.

Native tick windows are bounded to five minutes, compressed and checksummed with dtype metadata in the private ledger. Quotes must be finite and ordered; unavailable/empty windows, collector gaps over five minutes, intrawindow quote gaps over two minutes and stale tails censor outcomes. Stop crossings fill at the actual observed quote, including adverse gaps; targets fill conservatively at their limit. The common 24-hour horizon uses the first eligible quote thereafter. This is a static protective-exit counterfactual, not a replay of every original strategy's dynamic, session, portfolio or funding lifecycle. Independent scenarios do not enforce live addition, aggregate capital or loss gates.

Gross monetary conversion freezes the recorded source stop-risk valuation. That approximation is explicit; it is not certified historical conversion. Full net results require exact broker lifecycle evidence for commissions/fees, swap and execution adjustment through the existing cost matcher. Unknown components stay unknown. Late broker cost records may improve coverage without reopening or changing gross outcomes. Matched comparisons show both currency delta and normalized R because changed stops alter initial risk. Net deltas require both outcomes to have complete cost coverage.

## Artifacts and interpretation

Private `excursion_tests.sqlite3` retains profiles, training lineage, forward trials, tick windows and rejection/censor reasons. Old journals are read-only. The research API exposes `excursion_analysis`; the dashboard shows readiness and paired completion/cost coverage. Comparisons stay separate by context, symbol, cohort and variant. A profile is permission for shadow testing only. Tests must accumulate representative later data, assess drawdown/margin and survive cost/forward validation before any separate live-rule decision. ML01's existing model-support and validation requirements remain unchanged.

Initial deployment found 38 eligible instrument profiles across crossover, ema_cross, tsmom, kalman_trend and orb. Other catalog entries wait for support or compatible observations. No forward profitability is claimed at activation. Validation: 256 shared tests, 21 final focused excursion/cost checks and nine dashboard tests passed.

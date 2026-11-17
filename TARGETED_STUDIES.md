# Targeted forward strategy studies

The order-free `targeted_studies.py` engine reads original accepted paper proposals for five Aurum strategy identities: already-reversed `bollinger_reversed` and `zscore_reversed` (no second reversal), original `vol_squeeze`, `ema_cross` and `kalman_trend`. The predeclared versioned plan is `exness_arena/targeted_plan.json`. Enrollment begins only after the plan activation cutoff. Existing signals, live positions and previous experiment outcomes are not retroactively changed.

## Experiments

Reversed Bollinger and Z-score each compare original protection with: entry-price protection after +0.25 price-R; locking +0.25 price-R after +0.5R; a market exit after 50% giveback from a peak of at least +0.5R; 60-minute and 120-minute elapsed holding limits; and shadow portfolio admission limits of two or five open positions across instruments per strategy/cohort/variant.

EMA cross and Kalman trend compare original protection with the same entry-price and +0.25R lock, 50% giveback exit after +0.5R, and a 0.25R trail armed after +0.5R. Stops never widen. Hard protection is evaluated before an update; updates become effective on a later tick. Broker stop/freeze distance and tick-grid constraints can reject virtual updates. Giveback closes use the current observed quote, never the earlier peak. Entry-price protection is gross, not net break-even after costs.

Volatility squeeze compares original signals with three separate filters: direction aligned with the saved close/SMA50/SMA200 relationship; a saved ATR14/close band of 0.02%–0.5%; and saved quoted spread no greater than 0.05 original stop-distance. These thresholds are fixed exploratory probes, not optimized or recommended settings. Missing, invalid or noncausal features remain unknown, not evidence of a successful filter. Each arm changes one policy at a time; no winning combination is selected automatically.

## Replay and exposure

All variants use the same signal and observed bid/ask path, first eligible fill strictly after 250 ms and existing downward-only sizing, lot/stop/spread checks, execution reserve and 3.50 USC ceiling. SL gaps fill at the actual quote and TP fills at its limit. Initial monetary conversion freezes the source stop-risk valuation; historic conversion parity is not claimed. The original static-protection baseline retains the common 24-hour research horizon; elapsed time arms start at their modeled fill.

Version 1.1 conservatively censors changed bid/ask quotes sharing a millisecond; identical duplicate quotes are harmless. Earlier plan results stay separate. Provider windows are bounded, ordered and checksum-bound to symbol, range and dtype. Cross-instrument exposure decisions use a globally merged chronological event stream with deterministic symbol/source tie handling, not whole-symbol replay batches. Unknown paths or unresolved exposure cannot free admission capacity. Missing provider windows, stale tails and quote gaps censor results. Late-published source signals are flagged rather than retrospectively changing portfolio admission. Source IDs, original version identities, cohort, plan, method and rule snapshots remain in the separate private `targeted_studies.sqlite3` ledger.

Intentional filter/cap exclusions are zero-trade outcomes and compared against the entire matched baseline signal set. Excluded baseline winners therefore count as opportunity losses. Pending/unknown baselines are not treated as completed losses. Cost-qualified net comparisons require complete matching broker cash-cost evidence on both executed sides; excluded trades incur no hypothetical execution charge. Unknown costs are not zero-filled. Known prices, gross profits or entry-price stops alone do not certify net profit.

## Dashboard and limitations

Research → Targeted strategy risk tests shows full-signal paired deltas, exclusions/rejections/unknowns, net-cost coverage, concurrent floating-plus-realized gross P/L, sampled drawdown/underwater and current/peak standalone margin estimates. Cohorts and plans remain separate. Portfolio rows explicitly pool original instrument-specific versions; these descriptive aggregates are not isolated-version validation or a funded replication of live loss/addition gates. Margin excludes broker hedge offsets and tiers. Percentage drawdown and margin utilization have no invented capital baseline. Missing data and short sampled windows limit conclusions; no automatic promotion occurs.

Live exits, rules, risk/loss limits, drawdown protection, claims and ownership are unchanged. The two/five-position caps apply only to their shadow arms and do not restore any live combined-risk or position cap. Only the research collector needs a reload; strategy workers remain running. Private journals, ticks, account settings and position data are excluded from Git.

Validation: 280 shared tests passed, followed by 12 final targeted checks and nine dashboard core checks. Coverage includes causal long/short protection, actual-price stop gaps/giveback, holding-time clocks, rejected stop updates, cross-instrument chronological admission, unknown exposure, stale/future filters, missed winners and unknown costs.

# Strategy research and paper-only exit tests

Research is observational. The existing live guard, 3.50 USC per-position ceiling, 10% execution reserve, symbol lot caps, strategy position rules, loss gates, drawdown halt, ownership and server-side protection remain authoritative. Research never resets a halt, promotes a variant to live trading, changes parameters or modifies an existing broker position.

## Local records

`Monn/.exness-arena/research.sqlite3` is a separate SQLite WAL database. The live execution journal remains `trial.sqlite3`. Both stay private and excluded from Git.

| Record | Contents |
|---|---|
| `versions` | Source/configuration content hashes; all five Git heads and dirty flags; Python and relevant package versions; risk/rule configuration; Aurum artifact-manifest hash when present at evaluation start. Account login, server and credentials are omitted. |
| `inputs` | Content-addressed, compressed exact closed M15 OHLCV/spread windows consumed by each decision. Shared windows are deduplicated. They are not later re-downloaded or overwritten. |
| `research_decisions` | Repository, strategy, symbol, closed-bar timestamp, version/input references, observation time, quote and full broker contract snapshot, live flags, features, evaluation outcome and explanatory detail. No-signal decisions are retained. |
| `attempts` | Each proposed request, contemporaneous quote, unchanged live response or explicit paper-only response, exceptions, and broker order/deal identifiers. Includes blocked proposals. |
| `availability` | Missing data, unsupported profiles and quarantines, deduplicated by status/detail within each M15 observation interval. This preserves states that the live coverage table overwrites. |
| `live_deals` / `live_context` | Original broker deal payloads and exact opening-order/deal links to recorded live decisions and versions. Trades without a recorded match remain explicitly unknown. A paper-seed decision is never attributed to a real trade. |
| `live_initial_risk` / `live_path` | Initial stop risk when an opening record is known; sampled floating profit, swap, booked deal P&L/costs, quote, volume and protection. Samples support observed favorable/adverse excursion bounds in USC, and R where initial risk is known. |
| `shadows` / `shadow_events` | Persistent paper positions, immutable opening references, simulated exits, initial risk, gross USC, R-multiple and observed excursion bounds. |
| `shadow_rejections` / `shadow_variant_rejections` / `observations` | Why a paper candidate could not fit its sizing/market checks, and research-data failures. |

Generic features include SMA(20/50/100/200), TA-Lib ATR(14/48), RSI(2/14), the last closed bar and UTC hour. Adapters additionally record their available signal/forecast, original configuration or parameters, Aurum artifact manifest, Donchian native stop, SuperTrend lines/forming open and Sunrise signal/state detail. These are not claimed to be every internal indicator. The exact input window plus source/configuration version supports deeper replay and feature extraction later. Forming-bar extremes are not used as closed signal data.

Code fingerprints are cached for each running process; code changes require a controlled restart. Configuration versions are captured per decision. Hashes remain useful when a later commit changes the Git head. The hashes identify source files; private input windows are not a complete backup of old source. Retain the corresponding commits for reproducibility.

Decisions are recorded from deployment onward. Live evaluation still obeys the existing per-strategy bar claim. Research does not replay claimed bars into live trading. `--research-once` provides a separate, deduplicated `paper_seed` cohort using the latest available closed signal with an observed current quote. It never backfills a hypothetical historical fill. Live and seeded paper cohorts are reported separately; they must not be counted as independent evidence for the same signal.

## Shadow experiment fixed in advance

Plan `mfe-inversion-v2` applies only to newly observed entry proposals. Existing `targets-v1` shadows retain their original three exits and stored outcomes; no new variants are backfilled onto old signals. The plan ID is included in version configuration and summary grouping. Six independent paper scenarios are predeclared:

1. **`proposed`:** keep the strategy's proposed SL and TP, including no fixed TP where supported.
2. **`target_1r`:** keep the same SL and set the target one initial stop-distance from entry.
3. **`target_3r`:** keep the same SL and set the target three initial stop-distances from entry.

4. **`mfe_lock_05r`:** keep the original TP; after observing +0.5 initial price-R, move the virtual stop to entry. This protects entry price, not net break-even after costs.
5. **`mfe_trail_1r`:** keep the original TP; after observing +1 initial price-R, trail the observed favorable peak by 0.5 initial price-R. Never widen the virtual stop.
6. **`reversed_swap`:** use the opposite side's executable price from the same quote; the original fixed TP becomes the SL, and the original SL becomes the TP. No fixed original TP means unsupported, rather than inventing one.

Price-R is the initial entry-to-stop distance, held fixed as protection tightens. Reported monetary R is gross broker-valued P&L divided by that variant's own initial USC risk. The five forward scenarios use the same side, entry, volume and initial SL. The reversal is independently sized against its new stop; its risk and volume can differ. All variants retain the 3.50 USC ceiling, 10% sizing reserve, requested-volume ceiling and existing symbol lot caps. A reversal whose minimum lot, protection, spread or margin cannot fit is rejected individually without discarding valid forward tests. Rejection summaries retain variant, plan, version, cohort and reason.

MFE protection activates prospectively after a quote or completed bar reveals the favorable move. Existing protection is checked first; an ambiguous bar hitting the original stop cannot arm an MFE exit first. A stop activated inside a bar is not tested against that bar's earlier extrema: original protection is the conservative fallback for that overlapping bar. If a newly revealed bar peak already retraced past the tightened stop, exit at the observed bar close, never an invented earlier entry/peak fill. Virtual stop changes, activation times and observed peaks are persisted across restarts. This is a sampled execution model, not a tick-exact backtest. Volume is rounded down using the existing symbol cap, broker minimum/step/maximum and 3.50 USC risk ceiling with its 10% reserve. Entry stop-distance, spread and margin checks apply. Invalid or unaffordable proposals are retained as rejections, not forced into paper trades. Paper scenarios time out at the first eligible closed bar after 96 M15 intervals (24 elapsed hours) if neither protection level has been reached.

These are **independent per-signal counterfactuals**, not a simulated funded portfolio. The simulator deliberately observes proposals that live trading blocks, including during the drawdown halt. It does not simulate account/repo/strategy loss budgets, same-direction profitable-addition gates, shared collateral allocation or a portfolio liquidation policy. The live order outcome and flags are recorded alongside the signal so research can distinguish executable and blocked cases. Shadow losses never enter live loss counters.

The tracker marks shadows about every ten seconds, using observed quotes and completed M15 bars after entry. Full-bar highs/lows are used only for bars beginning after the observed entry; pre-entry extremes in the partial entry bar cannot influence a result. If an eligible OHLC bar touches both stop and target, assume the stop first. Stop gaps use the observed open/quote, so simulated stop losses may exceed planned risk. Target gaps fill at the target conservatively. Broker valuation converts price changes to USC. A restart resumes stored open paper scenarios without opening broker positions or repeating their seeds.

## Limitations and evaluation discipline

Shadow results are **gross hypothetical results**, not measured live returns. Entry/exit bid-ask spread is included; commissions, swap and extra execution slippage are not modeled. Historical short exits approximate ask OHLC with the bar's reported spread. Currency conversion uses broker valuation when an observation is processed, rather than an archived historical conversion curve. Tick sampling, downtime, missing bars and unobserved partial-entry-bar paths can miss a touch; they cannot provide exact tick-level reconstruction. Exit time within an OHLC bar is approximated by its close. Excursions on an ambiguous exit bar exclude unknowable later extremes. Live excursion samples also miss moves between samples and do not reconstruct a legacy position's earlier lifetime. Do not interpret sampled bounds as exact MFE/MAE.

Keep variants fixed while collecting a prospective cohort. Separate code/protection versions, seeded versus live-evaluation cohorts, costs, symbols and overlapping signal times when analyzing results. Hold out later data before selecting a change; one market session and correlated trades are not enough to establish an improvement. Nothing here automatically trains, optimizes or deploys a strategy.

Broker sizing reads run before the decision write transaction; the collector commits each shadow independently, with its state and events atomic. A bounded 30-second SQLite busy wait matches the live journal for synchronized M15 bursts. Broker orders are never retried on a logging failure.

Research failures are reported separately. They never retry a real submission or clear a live gate. A partially recorded decision stays `started`/`error` rather than being invented as a successful observation. Disk use grows with collected history; no automatic pruning or archival deletion is enabled.

## Inspect and export

The Arena dashboard at `http://127.0.0.1:8765/#research` has a dedicated paper-only research view grouped by repository, strategy, version, observation mode, experiment plan, variant and open/closed state, with filtering, sorting, pagination and local CSV export. The matched comparison panel joins the same decision and attempt to its original `proposed` exit within the same version and plan. It shows both-closed / eligible counts, pending pairs, missing baselines, baseline/alternative mean R, paired mean ΔR and paired gross USC. Unsupported variants are shown separately. Only both-closed pairs contribute P&L comparisons; that subset remains censored while other pairs are pending. No significance or winner is claimed. Outcome-status filtering affects the raw exit table, not pair completeness. Legacy and new plans stay separate. See `Monn/DASHBOARD.md` for all views and value definitions. `http://127.0.0.1:8765/api/research` exposes the summary and limitations separately from live results.

From the workspace parent in PowerShell:

```powershell
$env:PYTHONPATH = (Resolve-Path Monn).Path
./.arena-venv/Scripts/python.exe -m exness_arena.research status
./.arena-venv/Scripts/python.exe -m exness_arena.research export
```

Exports go to the private `Monn/.exness-arena/research-export/` directory as CSV tables and decompressed `inputs.jsonl`. They are research artifacts, not trading instructions. To seed the latest closed signal without sending orders, use a repo's guarded launcher with `--research-once`; repeat seeds with the same strategy/symbol/bar/version are ignored.

Update this guide, the shared policy template and all five `EXNESS_TRIAL.md` files whenever research assumptions or live trading rules change. Use `python Monn/sync_trading_docs.py --check` to verify documentation consistency.

## Disconnect recovery

The five runners and read-only tracker reconnect to the pinned terminal after confirmed disconnects, with bounded backoff and account/permission validation. No new entry signals are evaluated while disconnected. Durable live bar claims and paper decision keys remain intact, so an interrupted evaluation/submission is never replayed. Recovery resumes current closed-bar observations without inventing missed entry signals or clearing the drawdown halt, risk/loss limits, quarantines, baseline or ownership. Existing virtual positions resume their original marking model when data returns. See `EXNESS_TRIAL.md` for retry timing and failure distinctions.

AegisQuant qualified net-R labels use the separate offline pipeline documented in `aegisquant/REPLAY_LABELS.md`. Existing gross arena exit comparisons are not training labels. Receipt and replay verification, full dated execution evidence and 500 fit examples over 120 dates remain mandatory; no live settings or halt are changed.

## User-selected live candidates, 2026-11-16

All scan-enabled profiles remain observable, with live eligibility limited to `tradingbot/bollinger_reversed`, `tradingbot/zscore_reversed`, `tradingbot/vol_squeeze` and `Monn/price_action_1r`. Other prospective evaluations use `test_only`; startup `paper_seed` and historical observations are preserved. The new variants retain the original signal parameters and selected reversal/1R exits, with separate magic numbers and claims. The legacy price-action 1R recipe rounds the original stop before measuring the target distance. Source/artifact drift blocks entries. Historical selected cohorts are short and gross, with outstanding shadows; user authorization is not a claim of validated profitability. All existing loss counters and the account drawdown halt continue to apply. Aurum's existing repo losses still exhaust its allowance.

Validation: 231 shared tests, 9 dashboard core tests and 439 AegisQuant tests passed; final focused stage/research checks passed (41 tests), including no live fallback on a test-only journal failure and owned-position management. No live order is required for promotion testing.

## Shadow portfolio risk observations

The separate `shadow_portfolios.sqlite3` ledger records prospective realized-plus-floating gross scenario P/L, current and peak standalone margin, maximum sampled drawdown and observed underwater durations. No live settings, halts, losses or ownership are written by the sampler. Samples are attempted at most once per minute within the collector loop; availability and collector workload can leave gaps. Quotes with a small clock lead are waited for once, up to three seconds, without backdating. Missing observations remain unknown. Percentage drawdown and margin utilization require a declared hypothetical capital baseline and are not invented.

The dashboard preserves cohort, plan, exit and version lineage. Pooled versions are descriptive observations rather than isolated-version validation; filtering by a version selects portfolios containing that version, not a recalculated subset. Historical floating equity and margin are not reconstructed. Gaps and missing samples make drawdown and underwater duration lower bounds. Independent overlapping scenarios do not constitute an executable funded portfolio, and estimated standalone margin excludes broker offsets and tiers. Gross paths exclude fees, financing and additional slippage.

Reassessment on 2026-11-17: reversed Bollinger and reversed Z-score remain research leads; volatility squeeze is conditional because positive cumulative gross P/L coexists with a falling observation window and a meaningful observed drawdown. Price action 1R lacks enough new portfolio observations. The short, gapped window does not validate any strategy as risk-adjusted profitable. Promotion stages remain unchanged. The detailed local reassessment is private in `.exness-arena/shadow-portfolio-reassessment.json`. Validation: 236 shared tests and nine dashboard tests passed; the final portfolio-specific checks passed (four tests).

## Evidence-gated shadow costs and exit research

The collector reports attributed broker commissions/fees, swap and observed execution adjustments only for matching recorded executions. Exact opening fills can supply known opening costs; complete lifecycle evidence requires identical decision, side, volume, entry/exit price and UTC second, a single open/close and matching realized gross cash P/L. Differences in prices, holding periods or monetary conversion are not silently labeled slippage. Explicit observed zeros remain distinct from missing components. Known partial costs never produce a fully costed net result. Samples retain evidence hashes and independent gross/net paths; prior gross-only samples stay unchanged. External unattributed account charges are outside these trade-level figures.

On 2026-11-17, the selected reversed Bollinger, reversed Z-score and volatility-squeeze cohorts have no fully matched broker cost lifecycles; price action 1R has no new candidate shadows. This is consistent with live entries being halted. The first two remain gross research leads, volatility squeeze needs drawdown/regime review, and none is established as net-profitable or ready for a risk increase. Promotion stages remain unchanged.

MAE/MFE can generate strategy/version-specific stop and target hypotheses. Normalize by original risk or ATR and retain symbol/session and cost context. Use all eligible signals, not only winners. Existing stops censor later paths, so wider-stop proposals cannot be justified from truncated excursions. Extremes alone do not encode whether a proposed stop or target was hit first. Replay ordered bid/ask paths with financing, costs and portfolio exposure, then test frozen proposals on later untouched data. No excursion percentile is installed as a live exit automatically. Validation: 242 shared tests and nine dashboard core tests passed, plus 11 final focused cost/portfolio checks including full-lifecycle net cash attribution.

## MAE/MFE forward comparisons

See [EXCURSION_TESTS.md](EXCURSION_TESTS.md) for frozen quantile hypotheses, support/censor gates, exact cost coverage, artifact lineage and limitations. The standalone engine reads the original journal and writes only its own research ledger. Profiles and comparison readiness appear on the Research dashboard for every registered strategy. Insufficient support remains explicit; no historical calibration profits are counted as forward results.

## Explicit new live trial, 2026-11-17

The user authorized a fresh account-equity baseline and new loss-counting period for the four selected live candidates. Historical trade results, sampled paths, ownership and claims remain intact. The tracker queries from the original `history_start_epoch`; entry loss gates query from the current trial `start_epoch`. All existing position risk, lot, margin, spread, addition and loss ceilings remain enforced. Account drawdown retains its existing percentage against the fresh baseline, and can halt again. No strategy quarantine is cleared by this transition.

## Excursion blocker recovery

Fixed bootstrap probes now collect forward excursions for low-data contexts while the learned support gate stays intact. Strict same-context cohort transfer makes existing stateless calibration usable after demotion to test-only without relabeling outcomes. A read-only Aegis journal bridge observes fresh compatible protective intents and preserves all native live gates. The dashboard distinguishes missing signals, original data/model blockers and actual calibrated/bootstrap readiness. Unsupported/controller/multi-leg flows remain explicit. See `EXCURSION_TESTS.md` for scope, provenance and validation; no live execution rule or loss history is changed.

## Targeted risk probes

See [TARGETED_STUDIES.md](TARGETED_STUDIES.md) and the versioned targeted plan for the five-strategy forward-only experiment. Matched comparisons retain excluded opportunities and cost coverage; exposure admission uses a globally ordered cross-instrument tick stream. The standalone ledger and dashboard report portfolio risk without changing any live entry/exit, cap, loss gate, drawdown latch or ownership.
## Research sampler recovery

The running tracker uses `exness_arena/research_sampling.py`. It reads broker data before journal writes, reuses durable initial risk and buffers the unchanged shadow exit engine's SQL until valuation finishes. Atomic writes compare the full original shadow row before applying results, so concurrent updates are preserved. It has no order-submission interface and does not modify execution flags, strategy stages or ownership. Missing valuations remain recorded errors rather than fabricated outcomes. This addresses research-initialization lock failures without changing frozen live sources. Donchian resumes as `test_only` after its specific initialization quarantine is verified and removed; other halt flags and claimed bars remain untouched.

Sampler recovery validation: 285 shared tests passed in the restricted run; the one localhost HTTP test passed when rerun with socket access. Four focused regression checks cover competing journal writers during broker reads, cached initial risk, stale shadow updates and preparation failures. Frozen live source hashes remain unchanged.

### Follow-up recovery audit — 2026-11-17

The shared sampler fix also resolved the cause of eleven older paper-initialization quarantines: `MT5-Python-Trading/crossover`, `MT5-Python-Trading/combined`, `Forex-Trading-Bot/supertrend`, `tradingbot/ema_cross`, `tradingbot/zscore_fade`, and Monn's `ma_cross`, `ma_heikin_ashi`, `rsi_regular_divergence`, `macd_divergence`, `rsi_hidden_divergence`, and `break_strategy`.

Recovery checked the exact stored initialization error, confirmed each strategy remained `test_only`, verified unchanged frozen live hashes and no subsequent database-lock errors, then removed only those matching quarantine flags. Each strategy subsequently recorded evaluated paper decisions across the existing watchlist. Matching stale quarantine health rows were retired only after that evidence existed. Private recovery events retain per-strategy attribution. No worker restart, source change, bar-claim replay, promotion, risk/loss reset or account-halt change was needed.

AegisQuant's separate journal passed its integrity check and continues recording strategy observations. Missing/gapped history, incomplete reference ranges and fresh-quote eligibility remain data prerequisites, not initialization exceptions to clear. They remain enforced. Older unfinished decisions are retained as incomplete evidence; they are not reconstructed as trades or replayed by resetting claims. The synchronized trial guides and strategy catalog continue to match the unchanged live settings.

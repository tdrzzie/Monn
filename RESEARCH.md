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
| `shadow_rejections` / `observations` | Why a paper candidate could not fit its sizing/market checks, and research-data failures. |

Generic features include SMA(20/50/100/200), TA-Lib ATR(14/48), RSI(2/14), the last closed bar and UTC hour. Adapters additionally record their available signal/forecast, original configuration or parameters, Aurum artifact manifest, Donchian native stop, SuperTrend lines/forming open and Sunrise signal/state detail. These are not claimed to be every internal indicator. The exact input window plus source/configuration version supports deeper replay and feature extraction later. Forming-bar extremes are not used as closed signal data.

Code fingerprints are cached for each running process; code changes require a controlled restart. Configuration versions are captured per decision. Hashes remain useful when a later commit changes the Git head. The hashes identify source files; private input windows are not a complete backup of old source. Retain the corresponding commits for reproducibility.

Decisions are recorded from deployment onward. Live evaluation still obeys the existing per-strategy bar claim. Research does not replay claimed bars into live trading. `--research-once` provides a separate, deduplicated `paper_seed` cohort using the latest available closed signal with an observed current quote. It never backfills a hypothetical historical fill. Live and seeded paper cohorts are reported separately; they must not be counted as independent evidence for the same signal.

## Shadow experiment fixed in advance

For each new entry proposal, compare three independent paper scenarios:

1. **`proposed`:** keep the strategy's proposed SL and TP, including no fixed TP where supported.
2. **`target_1r`:** keep the same SL and set the target one initial stop-distance from entry.
3. **`target_3r`:** keep the same SL and set the target three initial stop-distances from entry.

All scenarios use the same entry side, observed executable ask/bid, volume and SL. Volume is rounded down using the existing symbol cap, broker minimum/step/maximum and 3.50 USC risk ceiling with its 10% reserve. Entry stop-distance, spread and margin checks apply. Invalid or unaffordable proposals are retained as rejections, not forced into paper trades. Paper scenarios time out at the first eligible closed bar after 96 M15 intervals (24 elapsed hours) if neither protection level has been reached.

These are **independent per-signal counterfactuals**, not a simulated funded portfolio. The simulator deliberately observes proposals that live trading blocks, including during the drawdown halt. It does not simulate account/repo/strategy loss budgets, same-direction profitable-addition gates, shared collateral allocation or a portfolio liquidation policy. The live order outcome and flags are recorded alongside the signal so research can distinguish executable and blocked cases. Shadow losses never enter live loss counters.

The tracker marks shadows about every ten seconds, using observed quotes and completed M15 bars after entry. Full-bar highs/lows are used only for bars beginning after the observed entry; pre-entry extremes in the partial entry bar cannot influence a result. If an eligible OHLC bar touches both stop and target, assume the stop first. Stop gaps use the observed open/quote, so simulated stop losses may exceed planned risk. Target gaps fill at the target conservatively. Broker valuation converts price changes to USC. A restart resumes stored open paper scenarios without opening broker positions or repeating their seeds.

## Limitations and evaluation discipline

Shadow results are **gross hypothetical results**, not measured live returns. Entry/exit bid-ask spread is included; commissions, swap and extra execution slippage are not modeled. Historical short exits approximate ask OHLC with the bar's reported spread. Currency conversion uses broker valuation when an observation is processed, rather than an archived historical conversion curve. Tick sampling, downtime, missing bars and unobserved partial-entry-bar paths can miss a touch; they cannot provide exact tick-level reconstruction. Exit time within an OHLC bar is approximated by its close. Excursions on an ambiguous exit bar exclude unknowable later extremes. Live excursion samples also miss moves between samples and do not reconstruct a legacy position's earlier lifetime. Do not interpret sampled bounds as exact MFE/MAE.

Keep variants fixed while collecting a prospective cohort. Separate code/protection versions, seeded versus live-evaluation cohorts, costs, symbols and overlapping signal times when analyzing results. Hold out later data before selecting a change; one market session and correlated trades are not enough to establish an improvement. Nothing here automatically trains, optimizes or deploys a strategy.

Research failures are reported separately. They never retry a real submission or clear a live gate. A partially recorded decision stays `started`/`error` rather than being invented as a successful observation. Disk use grows with collected history; no automatic pruning or archival deletion is enabled.

## Inspect and export

The Arena dashboard at `http://127.0.0.1:8765/#research` has a dedicated paper-only research view grouped by repository, strategy, version, observation mode, variant and open/closed state, with filtering, sorting, pagination and local CSV export. See `Monn/DASHBOARD.md` for all views and value definitions. `http://127.0.0.1:8765/api/research` exposes the summary and limitations separately from live results.

From the workspace parent in PowerShell:

```powershell
$env:PYTHONPATH = (Resolve-Path Monn).Path
./.arena-venv/Scripts/python.exe -m exness_arena.research status
./.arena-venv/Scripts/python.exe -m exness_arena.research export
```

Exports go to the private `Monn/.exness-arena/research-export/` directory as CSV tables and decompressed `inputs.jsonl`. They are research artifacts, not trading instructions. To seed the latest closed signal without sending orders, use a repo's guarded launcher with `--research-once`; repeat seeds with the same strategy/symbol/bar/version are ignored.

Update this guide, the shared policy template and all five `EXNESS_TRIAL.md` files whenever research assumptions or live trading rules change. Use `python Monn/sync_trading_docs.py --check` to verify documentation consistency.

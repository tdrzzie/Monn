# Local Arena dashboard

Open **http://127.0.0.1:8765/** on the computer running MT5. The dashboard is served by the existing tracker and refreshes about every ten seconds. It has no login, account switch, order, position-close, halt-reset or configuration-write actions. It binds only to loopback and loads no external scripts, fonts, analytics or services.

## Views

| View | Purpose |
|---|---|
| Overview | Tracked net, booked and floating P&L; account equity/balance/free margin; observed performance chart; repository cards; live strategy leaderboard; instrument exposure counts. |
| Strategies | Filter and sort registered strategy identities, magic numbers, statuses, closed/open counts, live results, win rates and sampled drawdowns. Open a strategy for its protection policy and entry-gate explanation. |
| Positions | Filter open positions or the latest 500 completed tracked lifecycles by repository, strategy, instrument and direction. Completed results include costs and partial/manual exits. Detail views show ownership and available observed excursions. |
| Research | Keep paper results distinct from live returns. Filter by strategy, full version prefix, seeded/live-evaluation cohort, exit variant and state. Version/cohort rows are never merged into a global profitability score. |
| Market monitoring | Review monetary and lot ceilings, the persistent entry halt, ownership/position rules, current instrument coverage and missing/unsupported data profiles. |
| Activity | Read the latest 30 execution events, grouping identical repository/strategy/kind/detail messages while retaining their repeat counts. |

Repository/strategy filters set the scope. Search filters detail lists; overview totals and its chart retain the repository/strategy scope. Other table-specific filters remain local to their view. Table headings sort, tables paginate, and charts have 1H/4H/all ranges and hover/left-right-key inspection. The compact layout has an accessible navigation menu. Only display currency and chart range are saved as browser preferences.

## Reading values correctly

- **100 USC = 1 USD.** Display currency switches presentation only. Risk-policy labels remain explicitly USC. CSV source fields retain their explicitly named USC/USD units, regardless of the display toggle.
- **Trial net = booked net + floating P&L.** Booked includes opening/closing deal fees, commission and swap; it can differ from completed-position P&L. Open-position floating values include position swap; opening fees are booked separately.
- **Strategy performance excludes manual/external exposure.** The account equity and its decline gate include it. An account decline threshold is an entry halt, not an automatic liquidation instruction or a guaranteed loss ceiling.
- **Scanning does not mean entry permission.** The persistent account drawdown halt is prominently shown even when every strategy process is scanning. Tracker snapshots older than 45 seconds and running process heartbeats older than 90 seconds are labelled stale; paper snapshots older than 90 seconds also show a warning.
- **History is observed data.** Chart queries read the live ledger with read-only SQLite connections. Charts display at most 300 actual observations, including endpoints; they do not fabricate or interpolate missing P&L samples. Downsampling can omit intermediate extremes. Strategy drawdown statistics remain the tracker's full sampled statistics.
- **Exposure bars count tickets**, with buys and sells shown separately; they are not comparable contract sizes or monetary risk. Distinct strategies may hold correlated trades.
- **Paper figures are gross hypothetical outcomes.** They include spread but exclude fees, swap and extra slippage; they are not live returns or a simulated funded portfolio. Read `Monn/RESEARCH.md` for the experiment assumptions and limitations.

CSV export downloads all matching rows from the current view, not just the visible page. Exports stay on the user's computer. Text fields are escaped and formula-like spreadsheet text is neutralized. No account identifiers or credentials are exposed by the new dashboard account snapshot. Raw local journals and private settings are not web routes.

## Implementation and operation

Static HTML/CSS/JavaScript lives in `Monn/exness_arena/dashboard/`; no build step or frontend dependency installation is needed. `tracker.py` serves an explicit allowlist of assets and read-only endpoints: `/api/results`, `/api/research`, `/api/history`, `/api/trades` and `/api/protection`. Private settings, databases and arbitrary filesystem paths cannot be downloaded through this handler. Existing local CSV/research export workflows remain available.

`Monn/reload_exness_dashboard.ps1` verifies the recorded collector's executable/command line, replaces only that read-only collector and checks dashboard readiness. It preserves the five strategy-process PIDs, broker positions, settings, STOP marker and all loss flags. Static asset-only edits need a browser reload; backend changes need the collector reload. Bot behavior changes still require the separate controlled trial restart.

For isolated UI review, `python -m exness_arena.dashboard_preview` with `Monn` on `PYTHONPATH` serves existing snapshots and read-only history on **127.0.0.1:8766**. It does not initialize MT5 or generate new trading/research decisions. Stop the preview when verification is complete. The deployed dashboard remains on port 8765.

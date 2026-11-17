# Enabled-compatible strategy catalog

The runtime enabled list is local in `Monn/.exness-arena/settings.json`. The original 31 profiles passed read-only native-data audits. Three derived profiles reuse their signal adapters with explicitly tested frozen execution transforms; all 34 are separately tracked. This establishes execution compatibility, not profitability.

| Repo | Strategy | Magic | Pair scope |
|---|---|---:|---|
| Monn | ma_cross | 26111301 | All 35 discovered instruments |
| Monn | break_strategy | 26111701 | All 35 discovered instruments |
| Monn | macd_divergence | 26111702 | All 35 discovered instruments |
| Monn | ma_heikin_ashi | 26111703 | All 35 discovered instruments |
| Monn | price_action | 26111704 | All 35 discovered instruments |
| Monn | rsi_hidden_divergence | 26111705 | All 35 discovered instruments |
| Monn | rsi_regular_divergence | 26111706 | All 35 discovered instruments |
| Monn | trend_following | 26111707 | All 35 discovered instruments |
| MT5-Python-Trading | crossover | 26111302 | All 35 discovered instruments |
| MT5-Python-Trading | smc | 26111601 | All 35 discovered instruments |
| MT5-Python-Trading | trend_breakout | 26111602 | All 35 discovered instruments |
| MT5-Python-Trading | combined | 26111603 | All 35 discovered instruments |
| tradingbot | ema_cross | 26111305 | All 35 discovered instruments |
| tradingbot | tsmom | 26111801 | All 35 discovered instruments |
| tradingbot | donchian | 26111802 | All 35 discovered instruments |
| tradingbot | kalman_trend | 26111803 | All 35 discovered instruments |
| tradingbot | zscore_fade | 26111804 | All 35 discovered instruments |
| tradingbot | rsi2 | 26111805 | All 35 discovered instruments |
| tradingbot | bollinger_revert | 26111806 | All 35 discovered instruments |
| tradingbot | vol_squeeze | 26111807 | All 35 discovered instruments |
| tradingbot | orb | 26111808 | All 35 discovered instruments |
| tradingbot | managed_gold | 26111809 | XAUUSDc |
| mt5_live_trading_bot | sunrise_eurusd | 26111303 | EURUSDc |
| mt5_live_trading_bot | sunrise_gbpusd | 26111402 | GBPUSDc |
| mt5_live_trading_bot | sunrise_xauusd | 26111403 | XAUUSDc |
| mt5_live_trading_bot | sunrise_audusd | 26111404 | AUDUSDc |
| mt5_live_trading_bot | sunrise_xagusd | 26111405 | XAGUSDc |
| mt5_live_trading_bot | sunrise_usdchf | 26111406 | USDCHFc |
| mt5_live_trading_bot | sunrise_eurjpy | 26111407 | EURJPYc |
| mt5_live_trading_bot | sunrise_usdjpy | 26111408 | USDJPYc |
| Forex-Trading-Bot | supertrend | 26111306 | All 35 discovered instruments |
| Monn | price_action_1r | 26112303 | All 35 discovered instruments; original price action, 1R target |
| tradingbot | bollinger_reversed | 26112301 | All 35 discovered instruments; reversed original signal, swapped SL/TP |
| tradingbot | zscore_reversed | 26112302 | All 35 discovered instruments; reversed original signal, swapped SL/TP |

## Trial execution conventions

Every strategy uses its own magic, position ownership, loss ledger, closed-bar deduplication and profitable-addition gate. Existing monetary limits and historical positions remain unchanged. A first buy and sell may coexist; subsequent same-direction positions require every existing position from that strategy on the pair/direction to be profitable after swap and known opening fees.

Monn uses the original M15 JSON definitions and original signal classes. It initializes indicators on prior closed history and advances the latest closed bar. Missing ZigZag structure is a wait state. TA-Lib compatibility returns full-series last scalars; pending entries and entries without stops are skipped, never converted to market orders. Existing MACross behavior is preserved.

MT5-Python-Trading uses its original crossover, SMC, trend-breakout and combined signal classes. The combined profile uses the original majority logic. Breakout support/resistance excludes the current signal bar. Execution retains the shared 2-ATR stop / 4-ATR target convention.

Aurum uses ten original non-trainable rule classes with default parameters. Each strategy/pair has a separate hash-verified native artifact. Required lookbacks are retained: 2000 bars for TSMOM, 1200 for Kalman, 8000 for managed gold (gold only), and 600 for other rules. Absolute forecast must reach 0.25; the existing protective fallback is 2 x ATR(14) SL / 4 x ATR(14) TP except where the original strategy supplies supported protection as detailed below. Donchian now uses its native entry-anchored stop (default 2.5 x ATR(48)), with no fixed TP. Other Aurum rules lack standalone SL/TP prices and retain the explicit 2 x ATR(14) SL / 4 x ATR(14) TP fallback. Native forecasts generate entries rather than automatically liquidating the hedged book on forecast reversal or zero.

Sunrise retains its eight original pair-specific classes/configurations and replay anchors. SuperTrend retains its original single strategy. Neither is duplicated under additional labels.

Unexpected live strategy evaluation errors quarantine that strategy persistently, leave protective stops intact, and allow sibling strategies to continue. A quarantined strategy cannot submit new entries until reviewed; there is no automatic error-halt reset.

Read-only `exness_trial.py --audit-all` evaluates registered strategies without enabling them or sending orders. The expanded 35-instrument universe passes 280 Monn, 140 MT5-Python-Trading, 316 Aurum and 35 SuperTrend strategy/instrument checks; Sunrise retains eight original profile checks. No orders are sent by these audits. The five approved CFDs are UKOILc, USOILc, USTECc, US30c and US500c. Gold-only managed_gold and pair-specific Sunrise profiles are not expanded. Index lot ceilings now match the broker minimums: USTECc 0.05, US30c 0.05 and US500c 0.14. Other instruments, including oil, retain a 0.01 lot ceiling. Index orders still require broker-valued stop risk within the 3.50 USC ceiling and its existing execution reserve; signals that cannot fit are skipped. Ordinary stop-risk and execution checks remain mandatory.

## Prospective research collection

All enabled profiles record versioned research observations and independent paper-only exit variants from deployment onward. Live loss gates and ownership remain unchanged; the drawdown halt is not reset. Exact decision inputs and source/configuration hashes support replay, while unmatched historical trades retain unknown versions. See `Monn/RESEARCH.md` for the proposed/1R/3R variants, separate seeded/live cohorts, sampled excursions, gross-cost limitations, local exports and validation discipline. Shadow outcomes are not live returns and do not automatically change a strategy.

## Strategies not enabled

- **Monn / zigzag_follower:** Lookup name exists, but no implementation or original configuration is present.
- **tradingbot / macro_factor:** No point-in-time DXY and real-yield feed configured.
- **tradingbot / risk_off:** No point-in-time VIX and DXY feed configured.
- **tradingbot / intraday_seasonality:** Requires a separately validated fitted cost-aware time-bucket artifact.
- **tradingbot / ml_gbm:** Requires a validated trained model and fitted feature pipeline.
- **tradingbot / meta_label:** Requires a validated trained primary/meta-label model and feature pipeline.
- **tradingbot / rl_ppo:** No validated trained policy artifact.
- **tradingbot / regime_switch:** No validated fitted regime model.
- **mt5_live_trading_bot / placeholder_signal_adapter:** Explicitly marked as placeholder upstream; not a separate validated strategy.

## Current shared trading rules

These shared guard rules apply to all configured repositories; AegisQuant adds its original stricter strategy/data gates without changing the existing five bots. Numeric values and enabled profiles are rendered from the local live settings; execution semantics are maintained alongside `Monn/exness_arena/guard.py`. USC means US cents (100 USC = USD 1).

| Rule | Current setting |
|---|---|
| Per-position stop-risk ceiling | 3.50 USC for every strategy |
| Combined open-risk cap | Disabled (`total_risk_budget_usc: null`); no combined open-risk cap |
| Default maximum order volume | 0.01 cent lots; unchanged for forex, metals, crypto and oil |
| Index-specific lot ceilings | US30c: 0.05, US500c: 0.14, USTECc: 0.05 cent lots; broker minimum/step still apply |
| Strategy loss budget | 5.2444 USC |
| Repository loss backstop | 5.2444 USC shared by that repo's strategies |
| Account drawdown entry halt | 1% equity decline from the current explicitly authorized trial baseline |
| Quote age ceiling | 30 seconds |
| Shared position-count ceiling | None; AegisQuant retains its stricter original limits below |
| Position-rule scope | Repository + strategy + pair + direction |

A strategy may hold a first buy and first sell on the same pair together. Every additional position in a direction requires **every existing position from that same strategy on that pair and direction** to be individually profitable after swap and known opening fees. Zero, negative or non-finite net profit blocks additions. A profitable basket cannot hide a losing position. Other strategies have independent position rules; an opposite signal does not automatically close existing positions.

The existing five bots require a fresh qualifying closed M15 signal. AegisQuant uses each specification's original closed-bar timeframe. Every entry must fit the per-position ceiling and remaining strategy/repo loss headroom. Sizing reserves 10% for execution drift, rounds volume down and skips signals when the broker's minimum volume cannot fit. Entry margin must not exceed 10% of free margin, and spread must not exceed 15% of stop distance. Equal ceilings do not imply equal exposure or trade counts.

Strategy/repo loss checks use booked deal P&L including swap, commissions and fees since the preserved trial start; they are net-loss thresholds, not gross-loss counters. Profits do not raise the configured ceilings. The account equity gate also includes manual/external positions. Loss gates block new entries; they do not automatically liquidate the book or guarantee a maximum realized loss. Stops may slip or gap.

Every position requires a broker-valued server-side stop. Stop removal/widening, pending entries, upward lot rounding, account switching and changes to sibling-strategy/manual positions are prohibited. Existing manual positions remain outside bot results. All registered trial positions retain ownership and stop validation, including positions from subsequently disabled strategies.

Missing account/history state, unavailable stop valuation, stale quotes, a missing/stale tracker heartbeat (90 seconds), unexpected trial pending orders and ambiguous submissions block new risk. Strategy evaluation failures quarantine only that strategy. Halts, quarantines, loss history and equity baselines never reset automatically on restart or a rule change.

### Connection recovery

Normal runners wait through confirmed MT5 disconnections, including initial startup, and reconnect only to the configured terminal without supplying login credentials or switching accounts. Retry waits are 5, 10, 20 and then 30 seconds repeatedly; each terminal initialization has a 5-second IPC timeout. Recovery rechecks the pinned Exness USC hedging account and automated-trading permissions before a fresh scan. Missing account/terminal state suspends entries. Wrong-account, disabled-automation and unknown operational failures remain fatal; online strategy failures still quarantine that strategy.

The dashboard reports `reconnecting` / `recovering` until a scan completes. The read-only tracker also reconnects and publishes a healthy heartbeat only after a successful collection. Its connection recovery ignores the strategy STOP marker, so monitoring remains independent of a manual trading pause. Runner retry waits check STOP at most every 0.5 seconds, with an in-progress terminal call bounded by its timeout. Preflight/research-once connection failures still return to the caller instead of waiting indefinitely.

Recovery never retries an order, clears a claimed signal bar, replays missed signal bars, edits protection, resets a halt/quarantine, changes risk limits or resets loss/ownership/baseline records. A bar claimed before interruption stays claimed even if its outcome is uncertain. On reconnect the runner refreshes current inventory and closed history, and evaluates only the latest unclaimed closed M15 signal. Scanning and shadow observations can resume while the persistent drawdown halt continues blocking live entries. There is no general crash/Windows-reboot supervisor or automatic halt reset.

### Stop-loss and take-profit policy

Original protection is used where the source strategy supplies executable price rules. Strategies without standalone price protection retain the explicit existing safety fallback; they are not described as having unique native SL/TP. A missing or invalid supported native stop skips the signal. All levels remain subject to the same broker validation and 3.50 USC risk ceiling; stops are never changed just to force a trade to fit.

| Strategy group | Protection mode for new entries | Source rules and limitations |
|---|---|---|
| Monn | original | All eight configured strategy orders retain their original SL and optional TP, including original fix_order/max_sl_pct configuration. No TP is added when absent. |
| mt5_live_trading_bot | original | All eight Sunrise profiles retain their original stop_level and take_level. |
| Forex-Trading-Bot | original | SuperTrend retains its original average-line TP and reward/1.2 SL formula. |
| tradingbot/donchian | original stop, no fixed TP | Native Donchian state machine exposes its fixed original entry-close +/- stop_atr * ATR(atr_n) stop: defaults 2.5 * ATR(48). Reuse that original signal-entry anchor for additions, not a new ATR stop. Convert mid level to bid/ask trigger using current half-spread. No fixed TP exists, so new orders carry TP=0. Missing/invalid native stops skip entries without fallback. The broker stop can trigger intrabar; upstream state uses closed-bar comparisons. |
| tradingbot/other | explicit protective fallback | ema_cross, tsmom, kalman_trend, zscore_fade, rsi2, bollinger_revert, vol_squeeze, orb and managed_gold expose forecasts/state exits rather than standalone broker SL/TP rules. Retain the existing 2 * ATR(14) SL and 4 * ATR(14) TP. |
| MT5-Python-Trading | explicit protective fallback | crossover, smc, trend_breakout and combined expose direction only. Original multi_symbol_robot sends sl=None/tp=None; its stop_loss_pips is a sizing input, not an attached protection rule. Retain existing 2 * ATR(14) SL and 4 * ATR(14) TP. |
| migration | new entries only | Existing broker positions keep their SL/TP. No stop widening/removal, mass position updates, forecast/time/channel liquidation, risk-budget change, halt reset, ownership change or decision replay. Dynamic/signal exits are not represented as invented static TP prices. |
| aegisquant | original strategy protection; readiness gated | 29 literal strategy/controller modules; market/structural/ATR protection remains strategy-specific. Controllers emit routing/filter intents and are not extra P&L streams. Pending orders and unqualified multi-leg execution are not silently emulated. Shared3.50USC risk ceiling and persistent halt remain authoritative. Native execution requires a separately verified cost/slippage/parity profile; missing prerequisites block orders. |
| promoted_variants | frozen user-selected shadow recipes | bollinger_reversed and zscore_reversed retain original Aurum signal parameters, reverse direction and swap the original TP/SL; size independently. price_action_1r retains original price-action parameters and adjusted SL, targeting one initial price-risk distance. vol_squeeze retains its existing original direction and 2 ATR(14) SL / 4 ATR(14) TP fallback. Original versions stay test-only except vol_squeeze. No historical positions are relabeled or modified. |

Every strategy has a stable identity and magic in `Monn/exness_arena/registry.json`. The shared journal attributes deals and positions by original opening ownership and stable position ID, including partial closes and manual exits. Results remain separate by repository and strategy. The tracker exports `results.csv`, `strategies.csv` and `positions.csv` under the local `Monn/.exness-arena/` directory.

AegisQuant research data can be sourced entirely through the pinned terminal API. Checksum-verified native BID M5 archives extend history without fabricating candles or historical asks. After a genuine gap, recursive indicators restart and must complete the original warmup before evaluation resumes. A broker tick up to 0.5 seconds ahead of the local clock is waited for; its timestamp is never backdated, and the unchanged freshness ceiling still applies. The worker separately records prospective contract/funding observations and closed five-minute native bid/ask tick windows, including empty/unavailable/invalid ranges. These are data records, not completed training labels. Today's funding and contracts are never projected backward as historical facts.

ML candidate fitting is isolated by child strategy/version, symbol, broker and cost version. It requires verified executable labels, at least 500 training examples across 120 dates, chronological splits, overlap purging and a declared maximum-horizon embargo. The last 20% stays reserved. Fitted candidates remain unapproved with live execution disabled; cost stress, forward validation and review remain required. Missing data or insufficient labels remains an explicit block. The existing gross exit shadows cannot be silently converted into these labels. See `aegisquant/RESEARCH_SOURCING.md` for commands, artifacts and limitations. The offline qualified net-R replay pipeline supports TF01, BR01, OR01, OR02, MR01 and BT01 captured signals with original exits, dated contract/cost/margin/conversion/financing evidence, immutable receipts and independent replay verification before training. Missing evidence and censored paths never become labels. Initial collected-data execution revalidated two signals, qualified zero and trained zero models; no automatic promotion or live policy change occurs. See `aegisquant/REPLAY_LABELS.md`.

## MAE/MFE forward exit experiments

The separate `excursion-forward-v1` engine freezes strategy/instrument/source/parameter/cohort-specific hypotheses before new evaluation signals: an MAE stop, MFE target and combined exit, compared with original static protection on identical recorded bid/ask ticks. Calibration requires 30 completed observations over two entry dates and five gross winners; startup seeds and future-known outcomes are excluded. Stop hypotheses stay within 0.25–1.0 original risk-distance; target hypotheses within 0.25–3.0. No in-sample tuning score is presented as forward performance. Missing support, incompatible intents, unavailable paths and unaffordable minimum lots remain explicit gates.

The first eligible fill after 250 ms is used without retry. Broker lot/stop checks, symbol caps, the 3.50 USC ceiling and reserve remain enforced. Missing/empty tick windows and gaps censor outcomes; no intrabar ordering is synthesized. This independent static-exit/24h experiment does not silently emulate every original dynamic/session or multi-leg lifecycle. Gross conversion uses the recorded source risk valuation; full net deltas require complete matched broker cost evidence on both sides. Uncertain costs remain unknown. Method/training/tick hashes, paired outcomes and cost coverage appear in the separate private ledger and dashboard. There is no automatic promotion or alteration of live strategy rules, risk limits, drawdown halt or ML model gates. See `Monn/EXCURSION_TESTS.md`.

## Excursion cold-start and source readiness

Low-data strategies can collect forward MAE/MFE with explicitly labelled fixed bootstrap probes (0.75 original stop-distance, 1R target and both). These are predeclared experiments, not learned thresholds; the learned 30-observation/two-date/five-winner support gate is unchanged. Original forward baseline excursions retain close-known timestamps and unique source IDs for later calibration. Existing skipped signals and calibration trades are not replayed as new tests. Calibrated fits, strict same-signal-context prospective cohort transfers and bootstrap probes remain separate by profile/variant/evaluation cohort. Transfer is allowed only for the stateless Monn, MT5-Python-Trading, Aurum and SuperTrend adapters; Sunrise and AegisQuant keep their cohort boundary.

A read-only AegisQuant journal observer can revalidate fresh compatible single-leg protective signals for provisional static-exit research without passing through live execution. Input/version/state validation, original eligibility/deferral checks, recorded tick-value gross sizing, the shared ceiling, reserve and conservative original equity cap remain enforced. Startup reconstructions, controllers, multi-leg/deferred flows and missing original data/model prerequisites remain gated. No native qualification, ML model approval, financing history or historical conversion is invented. The dashboard distinguishes no entry observations, source/model blockers, calibrated profiles, transferred calibration and bootstrap stages. All live exits, risk/loss limits, drawdown halt, promotion stages and per-strategy ownership remain unchanged. See `Monn/EXCURSION_TESTS.md`.

## Targeted strategy risk studies

AegisQuant data pipeline v3 caches only history continuity for an unchanged bar generation, rechecks the live tail and obtains a real quote after costly readiness/account reads. The five-second quote gate remains strict; a temporary quote failure skips only that observation. CFD intraday calendars use reviewed Exness regular sessions only within 2026-11-02 through 2026-11-21 UTC and expire on 2026-11-22; earlier/later history is not certified. Original exchange-session RTH history remains available independently. No missing candles, full UTC D1 bars, prices, funding or conversion history are fabricated. Native and public cent archives confirm some empty FX rollover intervals; the user chose to continue with existing data and strict gap/warm-up gates. Strategies without sufficient complete history remain blocked. Source/calendar/pipeline hashes accompany new input snapshots. Live rules, risk/loss limits, drawdown protection, promotion stages, claims and per-strategy ownership remain unchanged. See `aegisquant/DATA_RECOVERY.md`.

Research sampling uses `research_sampling.py` to finish broker reads and valuations before short journal write transactions. Already-recorded opening risk is reused; shadow updates are prepared with the unchanged exit engine and applied atomically only when the original row still matches. A concurrent sampler cannot overwrite newer state. This prevents slow broker reads from holding the research writer lock and blocking paper-decision initialization. Preparation failures leave the shadow unchanged. Recovery removes only a verified research-initialization quarantine for the affected test-only strategy; it never clears account/loss/drawdown halts, resets claims/history or retries broker orders. Live recipes, risk limits and per-strategy ownership remain unchanged.

The independent `targeted-risk-forward-v1.1` plan adds predeclared forward probes for already-reversed Bollinger/Z-score, volatility squeeze, EMA cross and Kalman trend. Reversed signals are not reversed again. Tests cover gross entry-price/partial-profit protection, quote-priced giveback exits, 60/120-minute holds, two/five-position shadow admission caps, causal trend/ATR/spread filters and a tighter peak trail. Parameters are frozen before new signals and change one policy per arm. They are hypotheses, not optimized live recommendations.

Exposure arms process globally chronological cross-instrument ticks. Intentional exclusions count against the full matched baseline signal set, including missed winners; unknown paths/exposure remain unknown. Net comparisons require complete execution-cost evidence. The dashboard shows paired gross/net deltas, coverage, aggregate floating-plus-realized P/L, sampled drawdown/underwater and estimated margin. No historical signals or existing positions are edited. All live exits, risk/loss limits, drawdown protection, position/addition rules, claims, ownership and promotion stages remain unchanged. Shadow caps do not impose live caps. See `Monn/TARGETED_STUDIES.md`.

## Shadow cost coverage

Gross scenario P/L remains distinct from fully costed net P/L. Broker commissions and fees are attributed only when a shadow matches the same recorded decision and exact broker opening fill (symbol, side, volume, price and UTC second). Complete lifecycle costs additionally require a single matching close, the same exit price/second/volume, complete cash fields and matching realized gross P/L. Swap is taken from that exact lifecycle, never inferred from a different holding period or today's swap tariff. Observed execution delta is zero only when actual fills and monetary gross exactly agree with the shadow; nonzero differences remain unknown without dated conversion/slippage evidence. Partial fills, ambiguous matches and missing cash fields prevent full qualification.

Known partial commissions, swap and execution adjustments are reported with coverage counts and evidence hashes. Missing components remain unknown, including explicit distinction from observed zero broker charges. Fully costed net P/L, net drawdown and net underwater durations require complete cost coverage for all trades in a scenario portfolio. Earlier gross-only samples are preserved and never relabeled as net history. No net ranking is claimed from partial coverage; source/variant/cohort identity remains intact. Costs do not modify live execution, promotions, limits or halts.

## Shadow portfolio analysis

Prospective shadow portfolios aggregate concurrent floating and realized gross P/L per repository, strategy, observation cohort, plan and exit variant. Version lineage and the number of pooled versions remain explicit; these descriptive aggregates are not isolated-version validation. Samples begin at deployment, with no reconstructed historical equity. Sampled maximum drawdown, current/longest observed time underwater, window P/L change and current/peak standalone margin estimates appear on the research dashboard. Quotes and broker valuations must be available; missing marks/margin remain unknown. Missing observations and gaps over 120 seconds break underwater episodes; durations and drawdown are observed lower bounds.

Margin sums current linear per-lot standalone broker estimates, excluding hedge offsets and tier effects. No hypothetical capital baseline is invented, so percentage drawdown and margin utilization are unknown. Portfolios combine independent scenarios without enforcing live capital, loss or addition gates. Gross paths exclude commission, swap and extra slippage. Profitable closed shadows or a short/flat sampled window do not establish acceptable portfolio risk or justify automatic promotion. All live risk limits, source/artifact pins, ownership, loss records and the drawdown halt remain unchanged.

## Trial periods and historical results

A user-authorized new trial sets its start time and equity baseline from the pinned account. Repo and strategy loss gates count deals from that new start; their existing monetary allowances remain unchanged. The account drawdown gate measures the existing percentage against the new baseline. The separately preserved `history_start_epoch` keeps all prior deals in dashboard totals and historical charts. Ownership, claims, journals, historical samples and previous trial settings remain recorded. A trial reset clears only the reviewed account drawdown latch; strategy quarantines and unrelated halts are not silently cleared. Trial metadata in the dashboard API distinguishes the current risk period from cumulative results. Reconnection never performs this reset.

## Strategy deployment stages

Scanning enablement and live entry eligibility are separate. Live candidates: `Monn/price_action_1r`, `tradingbot/vol_squeeze`, `tradingbot/bollinger_reversed`, `tradingbot/zscore_reversed`. Every other registered strategy is test-only: it continues eligible scans and prospective shadow tests, but the shared guard refuses new live entries. Test-only evaluations use an explicit `test_only` cohort, not reconstructed startup evidence. Existing owned positions may still receive their permitted closes/protection updates.

The selected recipes are frozen in `Monn/exness_arena/promotion_policy.json`: Aurum `bollinger_reversed` and `zscore_reversed` reverse the original signals and swap their original TP/SL; `vol_squeeze` retains its original direction and existing 2 ATR(14) stop / 4 ATR(14) target; Monn `price_action_1r` retains the original signal/configuration and tick-rounded adjusted stop with a 1R target. Derived variants have unique magics and separate claims/ownership; historical originals are never relabeled. Original signal sources, transforms and per-instrument Aurum artifacts are pinned; drift blocks entries instead of silently changing the selected rules. Artifact fingerprints remain private.

These are user-selected research candidates, not validated profitable strategies. Promotion never clears the account drawdown halt, strategy quarantine, repo/strategy loss history, baseline or claims. Repo and strategy losses in the current trial continue blocking entries when their allowances are exhausted; prior-trial results remain visible but do not consume a newly authorized trial allowance. Risk ceilings, minimum-lot checks, position/addition rules and the disabled combined cap remain unchanged. There is no automatic re-promotion or halt reset.

## Paper research plan

Plan `mfe-inversion-v2` adds prospective MFE entry-stop / peak-trail tests and independently sized reversed SL/TP signals alongside original / 1R / 3R exits. All retain the 3.50 USC sizing ceiling and symbol caps. Missing fixed TP and unaffordable minimum lots are explicit rejections. Legacy `targets-v1` observations stay unchanged; matched comparisons preserve strategy, version, plan and cohort identity and show pending pairs. These paper tests never change live rules or the drawdown halt. See `RESEARCH.md` for timing and cost limitations.

## AegisQuant registered profiles

Implementation is distinct from data/model/broker readiness. See `aegisquant/ACTIVATION.md`. Controllers do not have independent P/L.

| Strategy | Magic | Original timeframe | Explicit candidate mapping |
|---|---|---|---|
| TF01 | 26112201 | M15 | US500c, USTECc, US30c, XAUUSDc, EURUSDc, USDJPYc |
| TF02 | 26112202 | D1 | EURUSDc, USDJPYc, XAUUSDc, XAGUSDc, US500c, USOILc |
| BR01 | 26112203 | M15 | US30c, US500c, USTECc, XAUUSDc, GBPUSDc, EURUSDc |
| OR01 | 26112204 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| OR02 | 26112205 | M5 | US30c, US500c, USTECc |
| MR01 | 26112206 | H1 | EURUSDc, USDCHFc, USDJPYc, US500c |
| MR02 | 26112207 | RTH_D1 | US500c |
| MO01 | 26112208 | H1 | USTECc, US500c, XAUUSDc, USOILc, EURUSDc |
| PA01 | 26112209 | M15 | EURUSDc, GBPUSDc, XAUUSDc, US30c, USTECc |
| PA02 | 26112210 | H1 | EURUSDc, GBPUSDc, US500c, XAUUSDc |
| BT01 | 26112211 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT02 | 26112212 | H1 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT03 | 26112213 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT04 | 26112214 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT05 | 26112215 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT06 | 26112216 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT07 | 26112217 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT08 | 26112218 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT09 | 26112219 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| BT10 | 26112220 | M15 | EURUSDc, GBPUSDc, USDJPYc, GBPJPYc |
| SW01 | 26112221 | M5 | EURUSDc, GBPUSDc, GBPJPYc |
| SW02 | 26112222 | M15 | EURUSDc, GBPUSDc, USDJPYc, XAUUSDc, US30c |
| TS01 | 26112223 | D1 | EURUSDc, USDJPYc, GBPUSDc, XAUUSDc, XAGUSDc, US500c, USOILc |
| TS02 | 26112224 | D1 | EURUSDc, USDJPYc, GBPUSDc, XAUUSDc, XAGUSDc, US500c, USOILc |
| CA01 | 26112225 | D1 | AUDJPYc, NZDJPYc, USDJPYc, EURUSDc, GBPUSDc |
| SP01 | 26112226 | D1 | EURUSDc, GBPUSDc, USTECc, US500c |
| SP02 | 26112227 | D1 | EURUSDc, GBPUSDc, USTECc, US500c |
| RG01 | 26112228 | H1 | EURUSDc, USDJPYc |
| ML01 | 26112229 | CHILD_EVENT | EURUSDc, GBPUSDc, USDJPYc, US500c, USTECc, US30c, XAUUSDc |

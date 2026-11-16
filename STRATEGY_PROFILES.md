# Enabled-compatible strategy catalog

The runtime enabled list is local in `Monn/.exness-arena/settings.json`. These 31 profiles passed read-only native-data audits and use reviewed adapters. This establishes execution compatibility, not profitability.

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

These rules apply to all five repositories. Numeric values and enabled profiles are rendered from the local live settings; execution semantics are maintained alongside `Monn/exness_arena/guard.py`. USC means US cents (100 USC = USD 1).

| Rule | Current setting |
|---|---|
| Per-position stop-risk ceiling | 3.50 USC for every strategy |
| Combined open-risk cap | Disabled (`total_risk_budget_usc: null`); no combined open-risk cap |
| Default maximum order volume | 0.01 cent lots; unchanged for forex, metals, crypto and oil |
| Index-specific lot ceilings | US30c: 0.05, US500c: 0.14, USTECc: 0.05 cent lots; broker minimum/step still apply |
| Strategy loss budget | 5.2444 USC |
| Repository loss backstop | 5.2444 USC shared by that repo's strategies |
| Account drawdown entry halt | 1% equity decline from the preserved trial baseline |
| Quote age ceiling | 30 seconds |
| Position-count ceiling | None per strategy, pair, repository or account |
| Position-rule scope | Repository + strategy + pair + direction |

A strategy may hold a first buy and first sell on the same pair together. Every additional position in a direction requires **every existing position from that same strategy on that pair and direction** to be individually profitable after swap and known opening fees. Zero, negative or non-finite net profit blocks additions. A profitable basket cannot hide a losing position. Other strategies have independent position rules; an opposite signal does not automatically close existing positions.

Every new entry needs a fresh qualifying closed M15 signal and must fit the per-position ceiling and remaining strategy/repo loss headroom. Sizing reserves 10% for execution drift, rounds volume down and skips signals when the broker's minimum volume cannot fit. Entry margin must not exceed 10% of free margin, and spread must not exceed 15% of stop distance. Equal ceilings do not imply equal exposure or trade counts.

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

Every strategy has a stable identity and magic in `Monn/exness_arena/registry.json`. The shared journal attributes deals and positions by original opening ownership and stable position ID, including partial closes and manual exits. Results remain separate by repository and strategy. The tracker exports `results.csv`, `strategies.csv` and `positions.csv` under the local `Monn/.exness-arena/` directory.

## Paper research plan

Plan `mfe-inversion-v2` adds prospective MFE entry-stop / peak-trail tests and independently sized reversed SL/TP signals alongside original / 1R / 3R exits. All retain the 3.50 USC sizing ceiling and symbol caps. Missing fixed TP and unaffordable minimum lots are explicit rejections. Legacy `targets-v1` observations stay unchanged; matched comparisons preserve strategy, version, plan and cohort identity and show pending pairs. These paper tests never change live rules or the drawdown halt. See `RESEARCH.md` for timing and cost limitations.

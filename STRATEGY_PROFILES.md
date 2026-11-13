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

Aurum uses ten original non-trainable rule classes with default parameters. Each strategy/pair has a separate hash-verified native artifact. Required lookbacks are retained: 2000 bars for TSMOM, 1200 for Kalman, 8000 for managed gold (gold only), and 600 for other rules. Absolute forecast must reach 0.25; execution retains the existing 2-ATR stop / 4-ATR target convention. Native forecasts generate entries rather than automatically liquidating the hedged book on forecast reversal or zero.

Sunrise retains its eight original pair-specific classes/configurations and replay anchors. SuperTrend retains its original single strategy. Neither is duplicated under additional labels.

Unexpected live strategy evaluation errors quarantine that strategy persistently, leave protective stops intact, and allow sibling strategies to continue. A quarantined strategy cannot submit new entries until reviewed; there is no automatic error-halt reset.

Read-only `exness_trial.py --audit-all` evaluates registered strategies without enabling them or sending orders. The expanded 35-instrument universe passes 280 Monn, 140 MT5-Python-Trading, 316 Aurum and 35 SuperTrend strategy/instrument checks; Sunrise retains eight original profile checks. No orders are sent by these audits. The five approved CFDs are UKOILc, USOILc, USTECc, US30c and US500c. Gold-only managed_gold and pair-specific Sunrise profiles are not expanded. All three indices currently exceed the unchanged 0.01 order-volume cap and are scan-only until broker minimum volume fits; oil minimum volume is 0.01. Ordinary stop-risk and execution checks remain mandatory.

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
| Maximum order volume | 0.01 cent lots; broker minimum/step still apply |
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

Every strategy has a stable identity and magic in `Monn/exness_arena/registry.json`. The shared journal attributes deals and positions by original opening ownership and stable position ID, including partial closes and manual exits. Results remain separate by repository and strategy. The tracker exports `results.csv`, `strategies.csv` and `positions.csv` under the local `Monn/.exness-arena/` directory.

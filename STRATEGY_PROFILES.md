# Enabled-compatible strategy catalog

The runtime enabled list is local in `Monn/.exness-arena/settings.json`. These 31 profiles passed read-only native-data audits and use reviewed adapters. This establishes execution compatibility, not profitability.

| Repo | Strategy | Magic | Pair scope |
|---|---|---:|---|
| Monn | ma_cross | 26111301 | All 30 discovered pairs |
| Monn | break_strategy | 26111701 | All 30 discovered pairs |
| Monn | macd_divergence | 26111702 | All 30 discovered pairs |
| Monn | ma_heikin_ashi | 26111703 | All 30 discovered pairs |
| Monn | price_action | 26111704 | All 30 discovered pairs |
| Monn | rsi_hidden_divergence | 26111705 | All 30 discovered pairs |
| Monn | rsi_regular_divergence | 26111706 | All 30 discovered pairs |
| Monn | trend_following | 26111707 | All 30 discovered pairs |
| MT5-Python-Trading | crossover | 26111302 | All 30 discovered pairs |
| MT5-Python-Trading | smc | 26111601 | All 30 discovered pairs |
| MT5-Python-Trading | trend_breakout | 26111602 | All 30 discovered pairs |
| MT5-Python-Trading | combined | 26111603 | All 30 discovered pairs |
| tradingbot | ema_cross | 26111305 | All 30 discovered pairs |
| tradingbot | tsmom | 26111801 | All 30 discovered pairs |
| tradingbot | donchian | 26111802 | All 30 discovered pairs |
| tradingbot | kalman_trend | 26111803 | All 30 discovered pairs |
| tradingbot | zscore_fade | 26111804 | All 30 discovered pairs |
| tradingbot | rsi2 | 26111805 | All 30 discovered pairs |
| tradingbot | bollinger_revert | 26111806 | All 30 discovered pairs |
| tradingbot | vol_squeeze | 26111807 | All 30 discovered pairs |
| tradingbot | orb | 26111808 | All 30 discovered pairs |
| tradingbot | managed_gold | 26111809 | XAUUSDc |
| mt5_live_trading_bot | sunrise_eurusd | 26111303 | EURUSDc |
| mt5_live_trading_bot | sunrise_gbpusd | 26111402 | GBPUSDc |
| mt5_live_trading_bot | sunrise_xauusd | 26111403 | XAUUSDc |
| mt5_live_trading_bot | sunrise_audusd | 26111404 | AUDUSDc |
| mt5_live_trading_bot | sunrise_xagusd | 26111405 | XAGUSDc |
| mt5_live_trading_bot | sunrise_usdchf | 26111406 | USDCHFc |
| mt5_live_trading_bot | sunrise_eurjpy | 26111407 | EURJPYc |
| mt5_live_trading_bot | sunrise_usdjpy | 26111408 | USDJPYc |
| Forex-Trading-Bot | supertrend | 26111306 | All 30 discovered pairs |

## Trial execution conventions

Every strategy uses its own magic, position ownership, loss ledger, closed-bar deduplication and profitable-addition gate. Existing monetary limits and historical positions remain unchanged. A first buy and sell may coexist; subsequent same-direction positions require every existing position from that strategy on the pair/direction to be profitable after swap and known opening fees.

Monn uses the original M15 JSON definitions and original signal classes. It initializes indicators on prior closed history and advances the latest closed bar. Missing ZigZag structure is a wait state. TA-Lib compatibility returns full-series last scalars; pending entries and entries without stops are skipped, never converted to market orders. Existing MACross behavior is preserved.

MT5-Python-Trading uses its original crossover, SMC, trend-breakout and combined signal classes. The combined profile uses the original majority logic. Breakout support/resistance excludes the current signal bar. Execution retains the shared 2-ATR stop / 4-ATR target convention.

Aurum uses ten original non-trainable rule classes with default parameters. Each strategy/pair has a separate hash-verified native artifact. Required lookbacks are retained: 2000 bars for TSMOM, 1200 for Kalman, 8000 for managed gold (gold only), and 600 for other rules. Absolute forecast must reach 0.25; execution retains the existing 2-ATR stop / 4-ATR target convention. Native forecasts generate entries rather than automatically liquidating the hedged book on forecast reversal or zero.

Sunrise retains its eight original pair-specific classes/configurations and replay anchors. SuperTrend retains its original single strategy. Neither is duplicated under additional labels.

Unexpected live strategy evaluation errors quarantine that strategy persistently, leave protective stops intact, and allow sibling strategies to continue. A quarantined strategy cannot submit new entries until reviewed; there is no automatic error-halt reset.

Read-only `exness_trial.py --audit-all` evaluates registered strategies without enabling them or sending orders. The three expanded repos passed 240, 120 and 271 pair/strategy checks respectively.

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

## Combined-cap removal

At the user's request, `total_risk_budget_usc` is explicitly `null`: there is no combined open-risk cap. The 3.50 USC per-position cap, 0.01 cent-lot volume cap, strategy position/profitable-addition rules, 5.2444 USC strategy and repo loss budgets, 1% account drawdown entry halt, ownership, deduplication and tracking remain unchanged. Stop-risk continues to be valued by the broker and every position still requires a server-side stop. A missing or malformed combined-cap setting fails closed; only explicit null disables it. No loss counter, equity baseline or strategy halt is reset.

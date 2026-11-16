"""Render configured trial guides from local settings without publishing account pins.

Run from any directory: python Monn/sync_trading_docs.py [--check]
Rule semantics must also be reviewed against the shared guard when they change.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOS = ('Monn', 'MT5-Python-Trading', 'mt5_live_trading_bot', 'tradingbot', 'Forex-Trading-Bot', 'aegisquant')


def policy(c):
    if c['position_policy'] != 'hedge_with_profitable_additions':
        raise ValueError('Review and update the documentation template for the new position policy')
    cap = c['total_risk_budget_usc']
    combined = 'Disabled (`total_risk_budget_usc: null`); no combined open-risk cap' if cap is None else f'{cap:g} USC'
    overrides = ', '.join(f'{symbol}: {cap:g}' for symbol, cap in sorted(c.get('max_volume_by_symbol', {}).items())) or 'None'
    protection = json.loads((ROOT / 'exness_arena/protection.json').read_text(encoding='utf-8'))
    protection_rows = '\n'.join(f"| {name} | {spec['mode']} | {spec['rules']} |" for name, spec in protection.items())
    return f"""## Current shared trading rules

These shared guard rules apply to all configured repositories; AegisQuant adds its original stricter strategy/data gates without changing the existing five bots. Numeric values and enabled profiles are rendered from the local live settings; execution semantics are maintained alongside `Monn/exness_arena/guard.py`. USC means US cents (100 USC = USD 1).

| Rule | Current setting |
|---|---|
| Per-position stop-risk ceiling | {c['risk_budget_usc']:.2f} USC for every strategy |
| Combined open-risk cap | {combined} |
| Default maximum order volume | {c['max_volume']:g} cent lots; unchanged for forex, metals, crypto and oil |
| Index-specific lot ceilings | {overrides} cent lots; broker minimum/step still apply |
| Strategy loss budget | {c.get('strategy_loss_budget_usc', c['bot_loss_budget_usc']):.4f} USC |
| Repository loss backstop | {c['bot_loss_budget_usc']:.4f} USC shared by that repo's strategies |
| Account drawdown entry halt | {c['account_loss_fraction'] * 100:g}% equity decline from the preserved trial baseline |
| Quote age ceiling | {c['max_quote_age_seconds']:g} seconds |
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
{protection_rows}

Every strategy has a stable identity and magic in `Monn/exness_arena/registry.json`. The shared journal attributes deals and positions by original opening ownership and stable position ID, including partial closes and manual exits. Results remain separate by repository and strategy. The tracker exports `results.csv`, `strategies.csv` and `positions.csv` under the local `Monn/.exness-arena/` directory.
"""


def render(c, repo, rules):
    enabled = c['enabled_strategies'][repo]
    counts = ', '.join(f"{r}: {len(c['enabled_strategies'][r])}" for r in REPOS)
    return f"""# Exness cent trial: {repo}

Only `exness_trial.py` is the guarded live entry point. Original upstream entry points bypass this shared-account trial and must not be used. The shared execution package and tracker live in sibling `Monn/exness_arena`; keep the configured repositories in sibling folders.

## Enabled deployment

This repo is {'enabled' if repo in c['enabled_bots'] else 'disabled'} in the live configuration, with {len(enabled)} enabled profiles: {', '.join('`' + s + '`' for s in enabled)}.

Across the configured repos there are {sum(len(c['enabled_strategies'][r]) for r in REPOS)} enabled profiles ({counts}). Enabled configuration is not a claim of current process health or profitability. Consult the dashboard for running, quarantined and halted status.

Broker pair discovery is {'enabled' if c.get('discover_pairs') else 'disabled'}; the current watchlist contains {len(c['symbols'])} instruments. Inventory refreshes every minute. Compatible strategies scan discovered tradable cent currency, metal and crypto pairs plus the five approved oil/index CFDs: `UKOILc`, `USOILc`, `USTECc`, `US30c`, and `US500c`. Sunrise retains eight pair-specific profiles; Aurum `managed_gold` is gold-only. Disabled/close-only symbols, unapproved non-pair contracts and conversion indicators cannot receive new entries. Monn, MT5-Python-Trading, Aurum (except gold-only `managed_gold`) and SuperTrend include all five CFDs; Sunrise has no original oil/index profile and remains restricted to its eight original instruments. Each CFD signal must still pass broker history, stop valuation, minimum-volume, margin, spread and session/order checks; inclusion does not guarantee an entry. Broker specifications checked on 2026-11-13: oil minimum volume is 0.01; `USTECc`/`US30c` minimum volume is 0.05 and `US500c` is 0.14. Index-specific ceilings are configured at those broker minimums; all other symbols retain the default lot ceiling shown below. Index entries are eligible only when the actual broker-valued stop risk fits the unchanged 3.50 USC per-position ceiling, its existing 10% execution reserve and remaining loss headroom. The guard never rounds volume upward or tightens a strategy stop merely to fit a lot minimum. A later increase in broker minimum volume causes entries to be skipped until explicitly reviewed; ceilings do not rise automatically. Contract specifications can change; the live guard always rechecks broker limits. Missing history is reported per strategy/pair. See `Monn/STRATEGY_PROFILES.md` for strategy magics, pair scopes and execution conventions, and `Monn/exness_arena/compatibility.json` for exclusions. Claude Trader is skipped; EA31337 remains blocked and is not part of the active deployment.

{rules}
## Research logging and shadow testing

The local `Monn/.exness-arena/research.sqlite3` records durable decisions (including no-signal outcomes), exact compressed closed-bar inputs, quotes/contracts, available features, blocked proposals, code/configuration/dependency/artifact versions and sampled live trade paths. New live trades are linked by their actual opening order/deal; older unmatched versions remain unknown. The live journal and ownership records remain separate.

Paper-only plan `mfe-inversion-v2` compares the proposed exit, 1R and 3R targets, an entry-price stop after observed +0.5 price-R, a 0.5R peak trail after +1 price-R, and an opposite-side entry with the original fixed SL/TP swapped. Reversals require a fixed original TP and are independently resized within the unchanged 3.50 USC ceiling, 10% reserve and symbol lot caps; unsupported/unaffordable variants are recorded as rejections. MFE stops activate prospectively, never against earlier bar extrema. Legacy `targets-v1` observations retain their original three exits without backfilling. All paper variants retain the 24-hour elapsed-time horizon checked on eligible closed bars. The dashboard shows plan/version/cohort-separated matched original-versus-alternative R and gross USC comparisons, pending pairs and rejection reasons; incomplete cohorts are provisional. They continue observing proposals while the live drawdown halt remains enforced. These are independent per-signal counterfactuals, not a portfolio simulation: they do not simulate live loss/addition gates. Results include spread but exclude fees, swap and extra slippage. Conservative OHLC handling and sampled paths limit precision. No shadow variant changes live parameters or places an order. See `Monn/RESEARCH.md` for schemas, methodology, limitations and export commands. The dashboard and `/api/research` show paper results separately from live performance.

## Dashboard

The local Arena dashboard at `http://127.0.0.1:8765/` has separate Overview, Strategies, Positions, Research, Market monitoring and Activity views, with repository/strategy filtering, search, sorting, pagination, USC/USD presentation, observed P&L charts, ownership details and local CSV downloads. The account view includes external exposure; strategy results exclude it. Live halt status and stale data warnings remain visible. Paper experiments stay separate by strategy, version and cohort. No trading, configuration or halt-reset controls are exposed. See `Monn/DASHBOARD.md` for value definitions, export scope, read-only routes and collector-only reload instructions.

## AegisQuant activation and original rules

AegisQuant is registered as a sixth guarded worker with 29 separately attributed strategy/controller profiles and unique magic numbers. It scans only each specification's explicit candidate universe and timeframe; BT02 uses H1, sweep/NY-opening systems use M5, daily/monthly profiles require their original daily/session inputs. No nearest-symbol substitutions or M15 substitutions are permitted. Native BID M5 observations are aggregated to UTC intervals; required missing bars, an unverified broker calendar or price basis, unavailable financing/conversion, or missing formation/model inputs block that strategy. The versioned XNYS calendar handles cash holidays and early closes.

The user authorized activation after tests on 2026-11-16. The worker is activated for guarded observation and prospective research. The persistent shared drawdown halt remains active and blocks new live entries. An execution profile with verified broker costs/slippage/parity and the original portfolio/exit lifecycle is additionally required; missing qualification is an explicit block, not a paper fill called live trading. ML01 has no approved frozen artifact or sufficient labels; CA01 lacks the required point-in-time funding dataset; paired strategies require formation inputs and a reconciled non-atomic two-leg bridge. BTCTX01 remains reserved/disabled. Controllers route/filter child signals and do not create an extra P/L stream. Code implementation and runtime data/model readiness are distinct statuses.

AegisQuant's original one-position-per-strategy-symbol, no-pyramiding and conflict rules are stricter than the shared addition allowance and apply to AegisQuant only. Its own costed 0.25% sizing, four-position subportfolio, 1% stop-risk, 0.5% underlying and 2x notional checks cannot enlarge the 3.50 USC shared ceiling; its original daily/weekly/peak latches never reset automatically. They do not restore the removed shared combined open-risk cap or change the other five bots' rules. Native cost/parity and lifecycle qualification remains a required gate.

All original exits, structural/ATR stops, no-target variants, session exits, monthly rebalances and pair unwind intents remain separately identifiable in the source modules. Advanced target/leg intents are never silently submitted as single-leg market trades. The arena's existing six exit shadows remain gross independent counterfactuals with the documented 24-hour horizon; they are not a full AegisQuant portfolio or dynamic-exit backtest. AegisQuant's own journal stores exact compressed multi-timeframe inputs, state transitions, blocked reasons and original intents. Startup reconstruction has its own research cohort and cannot open a live trade. See `aegisquant/ACTIVATION.md` for complete readiness limits and the implementation issue reports.

## Operation and documentation

Dashboard: http://127.0.0.1:8765 . Runtime settings, account pins, credentials, baseline and journal stay local in `Monn/.exness-arena/` and are excluded from Git. The local settings are authoritative; these guides intentionally omit account identifiers and credentials.

Use `Monn/restart_exness_trial.ps1` for preflight and a controlled restart that preserves broker positions, ownership and loss records. With `Monn` on `PYTHONPATH`, `python -m exness_arena.control pause` blocks new entries and exits strategy runners; server-side stops remain and the tracker continues. Keep Windows and MT5 running. Confirmed disconnects recover within the running process as described above; other fatal exits require inspection and a controlled restart. Halts never reset automatically.

Whenever trading rules change, update their implementation and this shared policy template, render all configured guides with `python Monn/sync_trading_docs.py`, and verify `python Monn/sync_trading_docs.py --check` against the live settings. Update `Monn/STRATEGY_PROFILES.md` and compatibility/registry details when strategy scope changes. Confirm the live dashboard agrees with the intended policy before reporting deployment complete. Commit and push the documentation with the rule change; never publish private runtime files.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    c = json.loads((ROOT / '.exness-arena/settings.json').read_text(encoding='utf-8'))
    rules = policy(c)
    files = {ROOT.parent / repo / 'EXNESS_TRIAL.md': render(c, repo, rules) for repo in REPOS}
    catalog = ROOT / 'STRATEGY_PROFILES.md'
    text = catalog.read_text(encoding='utf-8').split('## Combined-cap removal')[0].split('## Current shared trading rules')[0].rstrip()
    text = re.sub(r'All \d+ discovered pairs', f"All {len(c['discovered_pairs'])} discovered instruments", text)
    text = re.sub(r'All \d+ discovered instruments', f"All {len(c['discovered_pairs'])} discovered instruments", text)
    files[catalog] = text + '\n\n' + rules.rstrip() + '\n\n## Paper research plan\n\nPlan `mfe-inversion-v2` adds prospective MFE entry-stop / peak-trail tests and independently sized reversed SL/TP signals alongside original / 1R / 3R exits. All retain the 3.50 USC sizing ceiling and symbol caps. Missing fixed TP and unaffordable minimum lots are explicit rejections. Legacy `targets-v1` observations stay unchanged; matched comparisons preserve strategy, version, plan and cohort identity and show pending pairs. These paper tests never change live rules or the drawdown halt. See `RESEARCH.md` for timing and cost limitations.\n'
    registry = json.loads((ROOT/'exness_arena/registry.json').read_text(encoding='utf-8'))
    if 'aegisquant' in registry:
        rows='\n'.join('| '+sid+' | '+str(v['magic'])+' | '+v['entry_timeframe']+' | '+', '.join(v['symbols'])+' |' for sid,v in registry['aegisquant']['strategies'].items())
        files[catalog] += '\n## AegisQuant registered profiles\n\nImplementation is distinct from data/model/broker readiness. See `aegisquant/ACTIVATION.md`. Controllers do not have independent P/L.\n\n| Strategy | Magic | Original timeframe | Explicit candidate mapping |\n|---|---|---|---|\n'+rows+'\n'
    stale = [p for p, content in files.items() if not p.exists() or p.read_text(encoding='utf-8') != content]
    if args.check:
        for p in stale:
            print('OUT OF DATE:', p.relative_to(ROOT.parent))
        if stale:
            raise SystemExit(1)
        print('All configured guides and the catalog match the local settings and shared policy template.')
    else:
        for p in stale:
            p.write_text(files[p], encoding='utf-8', newline='\n')
        print(f'Updated {len(stale)} documentation files; runtime settings and trading processes unchanged.')


if __name__ == '__main__':
    main()

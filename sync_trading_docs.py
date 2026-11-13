"""Render the five trial guides from local settings without publishing account pins.

Run from any directory: python Monn/sync_trading_docs.py [--check]
Rule semantics must also be reviewed against the shared guard when they change.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOS = ('Monn', 'MT5-Python-Trading', 'mt5_live_trading_bot', 'tradingbot', 'Forex-Trading-Bot')


def policy(c):
    if c['position_policy'] != 'hedge_with_profitable_additions':
        raise ValueError('Review and update the documentation template for the new position policy')
    cap = c['total_risk_budget_usc']
    combined = 'Disabled (`total_risk_budget_usc: null`); no combined open-risk cap' if cap is None else f'{cap:g} USC'
    overrides = ', '.join(f'{symbol}: {cap:g}' for symbol, cap in sorted(c.get('max_volume_by_symbol', {}).items())) or 'None'
    protection = json.loads((ROOT / 'exness_arena/protection.json').read_text(encoding='utf-8'))
    protection_rows = '\n'.join(f"| {name} | {spec['mode']} | {spec['rules']} |" for name, spec in protection.items())
    return f"""## Current shared trading rules

These rules apply to all five repositories. Numeric values and enabled profiles are rendered from the local live settings; execution semantics are maintained alongside `Monn/exness_arena/guard.py`. USC means US cents (100 USC = USD 1).

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
| Position-count ceiling | None per strategy, pair, repository or account |
| Position-rule scope | Repository + strategy + pair + direction |

A strategy may hold a first buy and first sell on the same pair together. Every additional position in a direction requires **every existing position from that same strategy on that pair and direction** to be individually profitable after swap and known opening fees. Zero, negative or non-finite net profit blocks additions. A profitable basket cannot hide a losing position. Other strategies have independent position rules; an opposite signal does not automatically close existing positions.

Every new entry needs a fresh qualifying closed M15 signal and must fit the per-position ceiling and remaining strategy/repo loss headroom. Sizing reserves 10% for execution drift, rounds volume down and skips signals when the broker's minimum volume cannot fit. Entry margin must not exceed 10% of free margin, and spread must not exceed 15% of stop distance. Equal ceilings do not imply equal exposure or trade counts.

Strategy/repo loss checks use booked deal P&L including swap, commissions and fees since the preserved trial start; they are net-loss thresholds, not gross-loss counters. Profits do not raise the configured ceilings. The account equity gate also includes manual/external positions. Loss gates block new entries; they do not automatically liquidate the book or guarantee a maximum realized loss. Stops may slip or gap.

Every position requires a broker-valued server-side stop. Stop removal/widening, pending entries, upward lot rounding, account switching and changes to sibling-strategy/manual positions are prohibited. Existing manual positions remain outside bot results. All registered trial positions retain ownership and stop validation, including positions from subsequently disabled strategies.

Missing account/history state, unavailable stop valuation, stale quotes, a missing/stale tracker heartbeat (90 seconds), unexpected trial pending orders and ambiguous submissions block new risk. Strategy evaluation failures quarantine only that strategy. Halts, quarantines, loss history and equity baselines never reset automatically on restart or a rule change.

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

Only `exness_trial.py` is the guarded live entry point. Original upstream entry points bypass this shared-account trial and must not be used. The shared execution package and tracker live in sibling `Monn/exness_arena`; keep the five forks in sibling folders.

## Enabled deployment

This repo is {'enabled' if repo in c['enabled_bots'] else 'disabled'} in the live configuration, with {len(enabled)} enabled profiles: {', '.join('`' + s + '`' for s in enabled)}.

Across the five repos there are {sum(len(c['enabled_strategies'][r]) for r in REPOS)} enabled profiles ({counts}). Enabled configuration is not a claim of current process health or profitability. Consult the dashboard for running, quarantined and halted status.

Broker pair discovery is {'enabled' if c.get('discover_pairs') else 'disabled'}; the current watchlist contains {len(c['symbols'])} instruments. Inventory refreshes every minute. Compatible strategies scan discovered tradable cent currency, metal and crypto pairs plus the five approved oil/index CFDs: `UKOILc`, `USOILc`, `USTECc`, `US30c`, and `US500c`. Sunrise retains eight pair-specific profiles; Aurum `managed_gold` is gold-only. Disabled/close-only symbols, unapproved non-pair contracts and conversion indicators cannot receive new entries. Monn, MT5-Python-Trading, Aurum (except gold-only `managed_gold`) and SuperTrend include all five CFDs; Sunrise has no original oil/index profile and remains restricted to its eight original instruments. Each CFD signal must still pass broker history, stop valuation, minimum-volume, margin, spread and session/order checks; inclusion does not guarantee an entry. Broker specifications checked on 2026-11-13: oil minimum volume is 0.01; `USTECc`/`US30c` minimum volume is 0.05 and `US500c` is 0.14. Index-specific ceilings are configured at those broker minimums; all other symbols retain the default lot ceiling shown below. Index entries are eligible only when the actual broker-valued stop risk fits the unchanged 3.50 USC per-position ceiling, its existing 10% execution reserve and remaining loss headroom. The guard never rounds volume upward or tightens a strategy stop merely to fit a lot minimum. A later increase in broker minimum volume causes entries to be skipped until explicitly reviewed; ceilings do not rise automatically. Contract specifications can change; the live guard always rechecks broker limits. Missing history is reported per strategy/pair. See `Monn/STRATEGY_PROFILES.md` for strategy magics, pair scopes and execution conventions, and `Monn/exness_arena/compatibility.json` for exclusions. Claude Trader is skipped; EA31337 remains blocked and is not part of this five-repo deployment.

{rules}
## Research logging and shadow testing

The local `Monn/.exness-arena/research.sqlite3` records durable decisions (including no-signal outcomes), exact compressed closed-bar inputs, quotes/contracts, available features, blocked proposals, code/configuration/dependency/artifact versions and sampled live trade paths. New live trades are linked by their actual opening order/deal; older unmatched versions remain unknown. The live journal and ownership records remain separate.

Paper-only shadows compare the proposed exit with 1R and 3R targets using the same stop and risk-sized volume, with a 24-hour elapsed-time horizon checked on eligible closed bars. They continue observing proposals while the live drawdown halt remains enforced. These are independent per-signal counterfactuals, not a portfolio simulation: they do not simulate live loss/addition gates. Results include spread but exclude fees, swap and extra slippage. Conservative OHLC handling and sampled paths limit precision. No variant changes live parameters or places an order. See `Monn/RESEARCH.md` for schemas, methodology, limitations and export commands. The dashboard and `/api/research` show paper results separately from live performance.

## Operation and documentation

Dashboard: http://127.0.0.1:8765 . Runtime settings, account pins, credentials, baseline and journal stay local in `Monn/.exness-arena/` and are excluded from Git. The local settings are authoritative; these guides intentionally omit account identifiers and credentials.

Use `Monn/restart_exness_trial.ps1` for preflight and a controlled restart that preserves broker positions, ownership and loss records. With `Monn` on `PYTHONPATH`, `python -m exness_arena.control pause` blocks new entries and exits strategy runners; server-side stops remain and the tracker continues. Keep Windows and MT5 running. There is no automatic restart or automatic halt reset.

Whenever trading rules change, update their implementation and this shared policy template, render all five guides with `python Monn/sync_trading_docs.py`, and verify `python Monn/sync_trading_docs.py --check` against the live settings. Update `Monn/STRATEGY_PROFILES.md` and compatibility/registry details when strategy scope changes. Confirm the live dashboard agrees with the intended policy before reporting deployment complete. Commit and push the documentation with the rule change; never publish private runtime files.
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
    files[catalog] = text + '\n\n' + rules.rstrip() + '\n'
    stale = [p for p, content in files.items() if p.read_text(encoding='utf-8') != content]
    if args.check:
        for p in stale:
            print('OUT OF DATE:', p.relative_to(ROOT.parent))
        if stale:
            raise SystemExit(1)
        print('All five guides and the catalog match the local settings and shared policy template.')
    else:
        for p in stale:
            p.write_text(files[p], encoding='utf-8', newline='\n')
        print(f'Updated {len(stale)} documentation files; runtime settings and trading processes unchanged.')


if __name__ == '__main__':
    main()

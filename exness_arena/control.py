"""Configure once, inspect, or pause entries; never silently reset the trial baseline."""
import argparse
import json
import time
from .common import settings, locked, event
import MetaTrader5 as native
from .common import STATE, REGISTRY, health
from .volume import INDEX_SYMBOLS, entry_volume_cap


def configure():
    STATE.mkdir(parents=True, exist_ok=True)
    target = STATE / "settings.json"
    if target.exists():
        raise SystemExit("Settings already exist; preserving baseline and loss budgets")
    terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not native.initialize(path=terminal_path):
        raise SystemExit("MT5 connection failed")
    try:
        account = native.account_info()
        ps, orders = native.positions_get(), native.orders_get()
        if account is None or account.currency != "USC" or account.margin_mode != 2 or "Exness" not in account.company:
            raise SystemExit("Expected the existing Exness USC hedging account")
        if ps is None or orders is None:
            raise SystemExit("Account state unavailable")
        magics = {v["magic"] for v in REGISTRY.values()}
        if any(p.magic in magics for p in ps) or any(o.magic in magics for o in orders):
            raise SystemExit("Reserved magic collision; reconcile before configuring")
        risk = account.equity * 0.001
        cfg = {"login": account.login, "server": account.server, "terminal_path": terminal_path,
            "initial_equity_usc": account.equity, "start_epoch": time.time(), "live": False,
            "symbols": ["EURUSDc", "XAUUSDc"],
            "symbol_map": {"EURUSD": "EURUSDc", "XAUUSD": "XAUUSDc", "XAUUSDm": "XAUUSDc"},
            "enabled_bots": ["Monn", "MT5-Python-Trading"], "risk_budget_usc": risk,
            "bot_loss_budget_usc": risk * 5, "total_risk_budget_usc": None,
            "account_loss_fraction": 0.01, "position_policy": "hedge_with_profitable_additions",
            "max_volume": 0.1, "max_quote_age_seconds": 30,
            "excluded_existing_positions": [p.identifier for p in ps]}
        target.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        for bot, spec in REGISTRY.items():
            health(bot, "configured" if spec["runner"] else "blocked", spec.get("blocked", "Preflight pending"))
        print(f"Configured pinned USC account; equal stop-risk cap {risk:.4f} USC (${risk/100:.5f}) per ready bot. Live remains disabled.")
    finally:
        native.shutdown()


def remove_combined_cap():
    if not (STATE / 'STOP').exists():
        raise SystemExit('Pause and verify the strategy runners have exited before changing the cap policy')
    with locked() as db:
        cfg = settings()
        before = dict(cfg)
        cfg['total_risk_budget_usc'] = None
        assert {k: v for k, v in cfg.items() if k != 'total_risk_budget_usc'} == {k: v for k, v in before.items() if k != 'total_risk_budget_usc'}
        path = STATE / 'settings.json'
        backup = STATE / ('settings.before-removing-combined-cap-' + str(time.time_ns()) + '.json')
        backup.write_text(json.dumps(before, indent=2), encoding='utf-8')
        temp = path.with_suffix('.next.json')
        temp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
        temp.replace(path)
        event(db, 'control', 'remove_combined_open_risk_cap', {'previous_cap_usc': before.get('total_risk_budget_usc'),
            'combined_cap_usc': None, 'all_other_settings_unchanged': True})
    print('Combined open-risk cap disabled. Every other setting, loss counter and position record is unchanged.')


def enable_index_minimum_lots():
    if not (STATE / 'STOP').exists():
        raise SystemExit('Pause and verify runners have exited before changing index lot caps')
    from .guard import Gateway, finite_positive
    cfg = settings()
    gateway = Gateway(native, 'Monn', cfg)
    if not gateway.initialize():
        raise SystemExit('Cannot validate the pinned MT5 account')
    try:
        caps = {}
        for symbol in INDEX_SYMBOLS:
            spec = native.symbol_info(symbol)
            if spec is None or spec.trade_mode != native.SYMBOL_TRADE_MODE_FULL or not finite_positive(spec.volume_min):
                raise SystemExit('Missing or non-tradable index specification: ' + symbol)
            caps[symbol] = spec.volume_min
        with locked() as db:
            before = settings()
            updated = dict(before, max_volume_by_symbol=caps)
            for symbol in INDEX_SYMBOLS:
                entry_volume_cap(symbol, updated)
            path = STATE / 'settings.json'
            (STATE / ('settings.before-index-minimum-lots-' + str(time.time_ns()) + '.json')).write_text(json.dumps(before, indent=2), encoding='utf-8')
            temp = path.with_suffix('.next.json')
            temp.write_text(json.dumps(updated, indent=2), encoding='utf-8')
            temp.replace(path)
            event(db, 'control', 'index_minimum_lot_caps', {'max_volume_by_symbol': caps, 'other_settings_unchanged': True})
        print('Index caps set to broker minimums: ' + json.dumps(caps) + '; all monetary limits unchanged.')
    finally:
        native.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configure", "pause", "status", "remove-combined-cap", "enable-index-minimum-lots"])
    args = parser.parse_args()
    if args.action == "configure":
        configure()
    elif args.action == "pause":
        (STATE / "STOP").touch()
        print("Entries paused and strategy runners will exit; server-side position stops remain active. Tracker stays running.")
    elif args.action == 'remove-combined-cap':
        remove_combined_cap()
    elif args.action == 'enable-index-minimum-lots':
        enable_index_minimum_lots()
    else:
        result = STATE / "results.json"
        print(result.read_text() if result.exists() else "Tracker has not produced a snapshot")


if __name__ == "__main__":
    main()

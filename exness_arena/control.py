"""Configure once, inspect, or pause entries; never silently reset the trial baseline."""
import argparse
import json
import time
import MetaTrader5 as native
from .common import STATE, REGISTRY, health


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
            "bot_loss_budget_usc": risk * 5, "total_risk_budget_usc": risk * 2,
            "account_loss_fraction": 0.01, "max_positions_per_pair_per_bot": 2,
            "max_volume": 0.1, "max_quote_age_seconds": 30,
            "excluded_existing_positions": [p.identifier for p in ps]}
        target.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        for bot, spec in REGISTRY.items():
            health(bot, "configured" if spec["runner"] else "blocked", spec.get("blocked", "Preflight pending"))
        print(f"Configured pinned USC account; equal stop-risk cap {risk:.4f} USC (${risk/100:.5f}) per ready bot. Live remains disabled.")
    finally:
        native.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configure", "pause", "status"])
    args = parser.parse_args()
    if args.action == "configure":
        configure()
    elif args.action == "pause":
        (STATE / "STOP").touch()
        print("Entries paused and strategy runners will exit; server-side position stops remain active. Tracker stays running.")
    else:
        result = STATE / "results.json"
        print(result.read_text() if result.exists() else "Tracker has not produced a snapshot")


if __name__ == "__main__":
    main()

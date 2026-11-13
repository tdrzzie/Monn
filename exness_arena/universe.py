"""Broker-discovered currency, metal and crypto pairs on the pinned cent account."""
import json
from .common import STATE, REGISTRY, event, locked, now, settings


def tradable_pairs(symbols, full_mode=4):
    if symbols is None:
        raise RuntimeError("Broker symbol inventory unavailable")
    return sorted(s.name for s in symbols
                  if s.name.endswith("c") and s.trade_mode == full_mode
                  and s.currency_base and s.currency_profit
                  and s.currency_base != s.currency_profit
                  and str(s.path).startswith("Cent\\"))


def refresh(gateway):
    """Preserve every monetary limit and keep existing owned positions manageable."""
    before = settings()
    if not before.get("discover_pairs", False):
        return before["symbols"]
    pairs = tradable_pairs(gateway.symbols_get(), gateway.SYMBOL_TRADE_MODE_FULL)
    if not pairs:
        raise RuntimeError("No fully tradable cent pairs found; preserving existing allowlist")
    # Keep positions from either trial repo manageable even if their symbol becomes close-only.
    owned = gateway.trial_position_symbols()
    with locked() as db:
        current = settings()
        current["symbols"] = sorted(set(pairs + owned + current.get("managed_only_symbols", [])))
        current["discovered_pairs"] = pairs
        current["symbol_map"].update({s[:-1]: s for s in pairs})
        if current != settings():
            path = STATE / "settings.json"
            temp = path.with_suffix(".universe.json")
            temp.write_text(json.dumps(current, indent=2), encoding="utf-8")
            temp.replace(path)
            event(db, "control", "pair_universe", {"pairs": pairs, "limits_unchanged": True})
    return current["symbols"]


def coverage(bot, symbol, status, detail="", bar=None):
    with locked() as db:
        db.execute("INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?,?)", (bot, symbol, now(), status, str(detail), bar))

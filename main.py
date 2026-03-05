#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OwlWatch — Monitor price drop protection and autosell positions. Uses the Springa
engine for thresholds and triggers; provides CLI, config, and state persistence.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import Springa engine
try:
    from Springa import (
        SpringaEngine,
        Position,
        PriceSnapshot,
        SellOrder,
        MockPriceFeed,
        create_default_engine,
        save_engine_state,
        load_engine_state,
        to_checksum_address,
        compute_drop_bps,
        compute_floor_price_wei,
        should_trigger_drop,
        should_trigger_floor,
        position_summary,
        order_summary,
        status_display,
        trigger_kind_display,
        engine_stats,
        position_report,
        positions_table,
        orders_table,
        parse_wei,
        format_eth,
        truncate_address,
        SPRG_GUARDIAN_ADDRESS,
        SPRG_TREASURY_ADDRESS,
        SPRG_STATUS_ACTIVE,
        SPRG_STATUS_SOLD,
        SPRG_VERSION,
        SPRG_PRESET_CONSERVATIVE,
        SPRG_PRESET_MODERATE,
        SPRG_PRESET_AGGRESSIVE,
        create_position_with_preset,
        engine_health,
        filter_positions_active,
        filter_positions_near_trigger,
        seed_default_whitelist,
        SPRG_ZeroAddress,
        SPRG_ZeroAmount,
        SPRG_PositionNotFound,
        SPRG_GuardianOnly,
        SPRG_NotKeeper,
    )
except ImportError:
    SpringaEngine = None
    create_default_engine = None
    save_engine_state = None
    load_engine_state = None

# -----------------------------------------------------------------------------
# OwlWatch constants
# ------------------------------------------------------------------------------

OWL_APP_NAME = "OwlWatch"
OWL_VERSION = "1.0.0"
OWL_CONFIG_DIR = ".owlwatch"
OWL_CONFIG_FILE = "config.json"
OWL_STATE_FILE = "state.json"
OWL_DEFAULT_GUARDIAN = SPRG_GUARDIAN_ADDRESS if "SPRG_GUARDIAN_ADDRESS" in dir() else "0xB2c5E8f1A4d7b0C3e6F9a2B5d8E1c4F7a0B3D6e9"
OWL_DEFAULT_TREASURY = SPRG_TREASURY_ADDRESS if "SPRG_TREASURY_ADDRESS" in dir() else "0x3D6f9A2c5E8b1D4e7F0a3C6d9B2e5F8a1C4d7E0"


# -----------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------

def get_config_path() -> Path:
    return Path.home() / OWL_CONFIG_DIR / OWL_CONFIG_FILE


def get_state_path() -> Path:
    return Path.home() / OWL_CONFIG_DIR / OWL_STATE_FILE


def load_config() -> Dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_config(config: Dict[str, Any]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


# -----------------------------------------------------------------------------
# Engine factory and state
# ------------------------------------------------------------------------------

def create_engine_from_config() -> Optional[SpringaEngine]:
    if SpringaEngine is None:
        return None
    config = load_config()
    guardian = config.get("guardian", OWL_DEFAULT_GUARDIAN)
    treasury = config.get("treasury", OWL_DEFAULT_TREASURY)
    prices = config.get("mock_prices", {})
    feed = MockPriceFeed(prices=prices) if prices else None
    engine = create_default_engine(guardian=guardian, treasury=treasury, price_feed=feed)
    if config.get("seed_whitelist", True) and seed_default_whitelist:
        seed_default_whitelist(engine)
    state_path = get_state_path()
    if state_path.exists():
        load_engine_state(engine, state_path)
    return engine


def persist_engine(engine: SpringaEngine) -> None:
    save_engine_state(engine, get_state_path())


# -----------------------------------------------------------------------------
# CLI: config
# ------------------------------------------------------------------------------

def cmd_config(args: List[str]) -> None:
    if not args:
        config = load_config()
        print(json.dumps(config, indent=2))
        return
    if args[0] == "set" and len(args) >= 3:
        config = load_config()
        config[args[1]] = args[2]
        save_config(config)
        print(f"Set {args[1]} = {args[2]}")
    elif args[0] == "get" and len(args) >= 2:
        config = load_config()
        print(config.get(args[1], ""))


# -----------------------------------------------------------------------------
# CLI: position create / list / get
# ------------------------------------------------------------------------------

def cmd_position_create(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 5:
        print("Usage: position create <owner> <asset_id> <amount_wei> <initial_price_wei> [drop_bps] [floor_bps]")
        return
    owner = to_checksum_address(args[1])
    asset_id = args[2]
    amount_wei = parse_wei(args[3])
    initial_price_wei = parse_wei(args[4])
    drop_bps = int(args[5]) if len(args) > 5 else 2000
    floor_bps = int(args[6]) if len(args) > 6 else 500
    try:
        pos = engine.create_position(owner, asset_id, amount_wei, initial_price_wei, drop_bps=drop_bps, floor_bps=floor_bps)
        persist_engine(engine)
        print(position_summary(pos))
    except Exception as e:
        print(f"Error: {e}")


def cmd_position_list(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    owner = to_checksum_address(args[1]) if len(args) > 1 else None
    positions = engine.list_positions(owner=owner)
    print(positions_table(positions, engine._price_feed))


def cmd_position_get(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: position get <position_id>")
        return
    pos = engine.get_position(args[1])
    if not pos:
        print("Position not found.")
        return
    report = position_report(pos, engine._price_feed)
    print(json.dumps(report, indent=2))


# -----------------------------------------------------------------------------
# CLI: trigger / scan
# ------------------------------------------------------------------------------

def cmd_trigger(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: trigger <position_id>")
        return
    order = engine.check_and_trigger(args[1])
    if order:
        persist_engine(engine)
        print("Triggered:", order_summary(order))
    else:
        print("No trigger.")


def cmd_scan(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    caller = to_checksum_address(args[1]) if len(args) > 1 else engine.keeper
    try:
        orders = engine.scan_all_positions(caller)
        persist_engine(engine)
        for o in orders:
            print(order_summary(o))
        print(f"Executed {len(orders)} orders.")
    except Exception as e:
        print(f"Error: {e}")


# -----------------------------------------------------------------------------
# CLI: stats / health
# ------------------------------------------------------------------------------

def cmd_stats(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    s = engine_stats(engine)
    print(json.dumps(s, indent=2))


def cmd_health(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    h = engine_health(engine)
    print(json.dumps(h, indent=2))


# -----------------------------------------------------------------------------
# CLI: orders
# ------------------------------------------------------------------------------

def cmd_orders(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    position_id = args[1] if len(args) > 1 else None
    orders = engine.list_orders(position_id=position_id)
    print(orders_table(orders))


# -----------------------------------------------------------------------------
# CLI: whitelist
# ------------------------------------------------------------------------------

def cmd_whitelist(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if not args:
        print(list(engine._whitelist))
        return
    if args[0] == "add" and len(args) > 1:
        engine.add_to_whitelist(args[1])
        persist_engine(engine)
        print(f"Added {args[1]}")
    elif args[0] == "remove" and len(args) > 1:
        engine.remove_from_whitelist(args[1])
        persist_engine(engine)
        print(f"Removed {args[1]}")


# -----------------------------------------------------------------------------
# CLI: price (mock)
# ------------------------------------------------------------------------------

def cmd_price(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: price get <asset_id> | price set <asset_id> <price_wei>")
        return
    if args[0] == "get":
        snap = engine._price_feed.get_price(args[1])
        if snap:
            print(f"{args[1]}: {snap.price_wei} wei")
        else:
            print("No price.")
    elif args[0] == "set" and len(args) >= 4:
        if isinstance(engine._price_feed, MockPriceFeed):
            engine._price_feed.set_price(args[1], parse_wei(args[2]))
            persist_engine(engine)
            print(f"Set {args[1]} = {args[2]}")
        else:
            print("Only mock feed supports set.")


# -----------------------------------------------------------------------------
# CLI: disable / enable
# ------------------------------------------------------------------------------

def cmd_disable(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 3:
        print("Usage: disable <position_id> <caller_address>")
        return
    try:
        pos = engine.disable_position(args[1], to_checksum_address(args[2]))
        persist_engine(engine)
        print("Disabled:", position_summary(pos))
    except Exception as e:
        print(f"Error: {e}")


def cmd_enable(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 3:
        print("Usage: enable <position_id> <caller_address>")
        return
    try:
        pos = engine.enable_position(args[1], to_checksum_address(args[2]))
        persist_engine(engine)
        print("Enabled:", position_summary(pos))
    except Exception as e:
        print(f"Error: {e}")


# -----------------------------------------------------------------------------
# CLI: near (positions near trigger)
# ------------------------------------------------------------------------------

def cmd_near(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    within_bps = int(args[1]) if len(args) > 1 else 500
    positions = filter_positions_near_trigger(engine.list_positions(), engine._price_feed, within_bps=within_bps)
    print(positions_table(positions, engine._price_feed))


# -----------------------------------------------------------------------------
# CLI: export / import
# ------------------------------------------------------------------------------

def cmd_export(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    path = args[1] if len(args) > 1 else None
    if not path:
        print(json.dumps(engine.export_state(), indent=2))
        return
    save_engine_state(engine, path)
    print(f"Exported to {path}")


def cmd_import(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: import <path>")
        return
    load_engine_state(engine, args[1])
    persist_engine(engine)
    print("Imported.")


# -----------------------------------------------------------------------------
# CLI: preset create
# ------------------------------------------------------------------------------

def cmd_preset(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 6:
        print("Usage: preset <conservative|moderate|aggressive> <owner> <asset_id> <amount_wei> <initial_price_wei>")
        return
    preset_name = args[1].lower()
    owner = to_checksum_address(args[2])
    asset_id = args[3]
    amount_wei = parse_wei(args[4])
    initial_price_wei = parse_wei(args[5])
    try:
        pos = create_position_with_preset(engine, owner, asset_id, amount_wei, initial_price_wei, preset=preset_name)
        persist_engine(engine)
        print(position_summary(pos))
    except Exception as e:
        print(f"Error: {e}")


# -----------------------------------------------------------------------------
# CLI: version / help
# ------------------------------------------------------------------------------

def cmd_version() -> None:
    print(f"{OWL_APP_NAME} {OWL_VERSION} (Springa {SPRG_VERSION if 'SPRG_VERSION' in dir() else 'N/A'})")


def cmd_help() -> None:
    print("OwlWatch — Price drop protection & autosell monitor")
    print("  config [set <key> <value> | get <key>]")
    print("  position create <owner> <asset_id> <amount_wei> <initial_price_wei> [drop_bps] [floor_bps]")
    print("  position list [owner]")
    print("  position get <position_id>")
    print("  trigger <position_id>")
    print("  scan [keeper_address]")
    print("  stats")
    print("  health")
    print("  orders [position_id]")
    print("  whitelist [add|remove <asset_id>]")
    print("  price get <asset_id> | price set <asset_id> <price_wei>")
    print("  disable <position_id> <caller>")
    print("  enable <position_id> <caller>")
    print("  near [within_bps]")
    print("  export [path]")
    print("  import <path>")
    print("  preset <conservative|moderate|aggressive> <owner> <asset_id> <amount_wei> <initial_price_wei>")
    print("  report [path]")
    print("  updatehwm <position_id> <caller> <new_price_wei>")
    print("  batchtrigger <position_id1> [position_id2 ...]")
    print("  audit [path]")
    print("  watch [interval_sec] [keeper_address]")
    print("  validateaddress <address>")
    print("  info")
    print("  about")
    print("  dropbps <high_wei> <current_wei>")
    print("  floorprice <high_wei> <floor_bps>")
    print("  reset yes")
    print("  active")
    print("  csv [path]")
    print("  simulate <position_id>")
    print("  refreshhwm [caller]")
    print("  cooldown <position_id>")
    print("  init")
    print("  sold")
    print("  summary")
    print("  byasset <asset_id>")
    print("  counts")
    print("  paths")
    print("  setprices <asset_id> <price_wei> [...]")
    print("  whoami <address>")
    print("  mypositions <owner_address>")
    print("  ordercount [position_id]")
    print("  backup [path]")
    print("  restore <path>")
    print("  healthshort")
    print("  assets")
    print("  status <position_id>")
    print("  positionjson <position_id>")
    print("  presets")
    print("  configdump")
    print("  engineconfig")
    print("  appinfo")
    print("  version")
    print("  help")


# -----------------------------------------------------------------------------
# Report generation
# ------------------------------------------------------------------------------

def generate_report(engine: SpringaEngine) -> Dict[str, Any]:
    positions = engine.list_positions()
    orders = engine.list_orders()
    active = filter_positions_active(positions)
    stats = engine_stats(engine)
    return {
        "generated_at": time.time(),
        "stats": stats,
        "positions_count": len(positions),
        "orders_count": len(orders),
        "active_count": len(active),
        "config": engine.get_config(),
    }


def cmd_report(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    path = args[1] if len(args) > 1 else None
    report = generate_report(engine)
    if path:
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to {path}")
    else:
        print(json.dumps(report, indent=2))


# -----------------------------------------------------------------------------
# Update high water mark
# ------------------------------------------------------------------------------

def cmd_update_hwm(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 4:
        print("Usage: updatehwm <position_id> <caller> <new_price_wei>")
        return
    try:
        pos = engine.update_high_water_mark(args[1], to_checksum_address(args[2]), parse_wei(args[3]))
        persist_engine(engine)
        print(position_summary(pos))
    except Exception as e:
        print(f"Error: {e}")


# -----------------------------------------------------------------------------
# Batch trigger check
# ------------------------------------------------------------------------------

def cmd_batch_trigger(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: batchtrigger <position_id1> [position_id2 ...]")
        return
    for pid in args[1:]:
        order = engine.check_and_trigger(pid)
        if order:
            print("Triggered:", order_summary(order))
    persist_engine(engine)


# -----------------------------------------------------------------------------
# Audit snapshot
# ------------------------------------------------------------------------------

def cmd_audit(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    try:
        from Springa import audit_snapshot
        snap = audit_snapshot(engine)
        path = args[1] if len(args) > 1 else None
        if path:
            with open(path, "w") as f:
                json.dump(snap, f, indent=2)
            print(f"Audit saved to {path}")
        else:
            print(json.dumps(snap, indent=2))
    except ImportError:
        print("audit_snapshot not available.")


# -----------------------------------------------------------------------------
# Watch loop (periodic scan)
# ------------------------------------------------------------------------------

def cmd_watch(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    interval = float(args[1]) if len(args) > 1 else 60.0
    caller = to_checksum_address(args[2]) if len(args) > 2 else engine.keeper
    print(f"Watching every {interval}s (keeper={truncate_address(caller)})")
    try:
        while True:
            orders = engine.scan_all_positions(caller)
            if orders:
                for o in orders:
                    print(f"[{time.strftime('%H:%M:%S')}] {order_summary(o)}")
                persist_engine(engine)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped.")


# -----------------------------------------------------------------------------
# Address validation
# ------------------------------------------------------------------------------

def cmd_validate_address(args: List[str]) -> None:
    if len(args) < 1:
        print("Usage: validateaddress <address>")
        return
    try:
        from Springa import validate_address
        ok = validate_address(args[0])
        print("Valid" if ok else "Invalid")
        if ok:
            print(to_checksum_address(args[0]))
    except Exception:
        print("Invalid")


# -----------------------------------------------------------------------------
# Info / about
# ------------------------------------------------------------------------------

def cmd_info(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    print(json.dumps(engine.get_config(), indent=2))


def cmd_about() -> None:
    print(f"{OWL_APP_NAME} v{OWL_VERSION}")
    print("Monitor price drop protection and autosell (Springa engine).")
    print("Config:", get_config_path())
    print("State:", get_state_path())


# -----------------------------------------------------------------------------
# Drop BPS calculator
# ------------------------------------------------------------------------------

def cmd_dropbps(args: List[str]) -> None:
    if len(args) < 3:
        print("Usage: dropbps <high_wei> <current_wei>")
        return
    high = parse_wei(args[1])
    current = parse_wei(args[2])
    bps = compute_drop_bps(high, current)
    print(f"Drop: {bps} bps ({100 * bps / 10000:.2f}%)")


# -----------------------------------------------------------------------------
# Floor price calculator
# ------------------------------------------------------------------------------

def cmd_floorprice(args: List[str]) -> None:
    if len(args) < 3:
        print("Usage: floorprice <high_wei> <floor_bps>")
        return
    high = parse_wei(args[1])
    floor_bps = int(args[2])
    floor_wei = compute_floor_price_wei(high, floor_bps)
    print(f"Floor: {floor_wei} wei ({format_eth(floor_wei)})")


# -----------------------------------------------------------------------------
# Reset state (clear positions/orders in memory and optionally save)
# ------------------------------------------------------------------------------

def cmd_reset(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    confirm = args[1] if len(args) > 1 else ""
    if confirm != "yes":
        print("Run: reset yes  to clear state.")
        return
    engine._positions.clear()
    engine._sell_orders.clear()
    persist_engine(engine)
    print("State cleared.")


# -----------------------------------------------------------------------------
# List active only
# ------------------------------------------------------------------------------

def cmd_active(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    positions = filter_positions_active(engine.list_positions())
    print(positions_table(positions, engine._price_feed))


# -----------------------------------------------------------------------------
# History log (append-only)
# ------------------------------------------------------------------------------

def get_history_path() -> Path:
    return Path.home() / OWL_CONFIG_DIR / "history.jsonl"


def log_action(action: str, data: Dict[str, Any]) -> None:
    path = get_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({"t": time.time(), "action": action, **data}) + "\n")


# -----------------------------------------------------------------------------
# Format wei for display
# ------------------------------------------------------------------------------

def format_wei_short(wei: int) -> str:
    if wei >= 1e18:
        return f"{wei / 1e18:.4f} ETH"
    if wei >= 1e9:
        return f"{wei / 1e9:.2f} Gwei"
    return str(wei) + " wei"


# -----------------------------------------------------------------------------
# Position CSV export
# ------------------------------------------------------------------------------

def export_positions_csv(engine: SpringaEngine, path: Optional[Path] = None) -> str:
    positions = engine.list_positions()
    lines = ["position_id,owner,asset_id,amount_wei,high_water_mark_wei,floor_price_wei,drop_bps,floor_bps,status"]
    for p in positions:
        lines.append(f"{p.position_id},{p.owner},{p.asset_id},{p.amount_wei},{p.high_water_mark_wei},{p.floor_price_wei},{p.drop_bps},{p.floor_bps},{status_display(p.status)}")
    csv = "\n".join(lines)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(csv)
    return csv


def cmd_csv(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    path = Path(args[1]) if len(args) > 1 else None
    csv = export_positions_csv(engine, path)
    if not path:
        print(csv)


# -----------------------------------------------------------------------------
# Simulate trigger (dry run)
# ------------------------------------------------------------------------------

def cmd_simulate(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: simulate <position_id>")
        return
    pos = engine.get_position(args[1])
    if not pos:
        print("Position not found.")
        return
    snap = engine._price_feed.get_price(pos.asset_id)
    if not snap:
        print("No price for asset.")
        return
    from Springa import would_trigger_at_price
    would = would_trigger_at_price(pos, snap.price_wei)
    drop_bps = compute_drop_bps(pos.high_water_mark_wei, snap.price_wei)
    print(f"Current price: {snap.price_wei} wei")
    print(f"Drop from HWM: {drop_bps} bps")
    print(f"Would trigger: {would}")


# -----------------------------------------------------------------------------
# Refresh HWM from feed
# ------------------------------------------------------------------------------

def cmd_refresh_hwm(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    try:
        from Springa import refresh_high_water_marks_from_feed
        caller = to_checksum_address(args[1]) if len(args) > 1 else engine.guardian
        n = refresh_high_water_marks_from_feed(engine, caller)
        persist_engine(engine)
        print(f"Updated {n} positions.")
    except ImportError:
        print("refresh_high_water_marks_from_feed not available.")


# -----------------------------------------------------------------------------
# Cooldown status
# ------------------------------------------------------------------------------

def cmd_cooldown(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: cooldown <position_id>")
        return
    pos = engine.get_position(args[1])
    if not pos:
        print("Position not found.")
        return
    try:
        from Springa import cooldown_remaining_sec, is_in_cooldown
        rem = cooldown_remaining_sec(pos)
        in_cd = is_in_cooldown(pos)
        print(f"In cooldown: {in_cd}")
        print(f"Remaining: {rem:.0f} sec")
    except ImportError:
        print("Cooldown helpers not available.")


# -----------------------------------------------------------------------------
# Default config init
# ------------------------------------------------------------------------------

def cmd_init(args: List[str]) -> None:
    path = get_config_path()
    if path.exists():
        print("Config already exists.")
        return
    default = {
        "guardian": OWL_DEFAULT_GUARDIAN,
        "treasury": OWL_DEFAULT_TREASURY,
        "seed_whitelist": True,
        "mock_prices": {},
    }
    save_config(default)
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Initialized config at", path)


# -----------------------------------------------------------------------------
# List sold positions
# ------------------------------------------------------------------------------

def cmd_sold(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    positions = [p for p in engine.list_positions() if p.status == SPRG_STATUS_SOLD]
    print(positions_table(positions, engine._price_feed))


# -----------------------------------------------------------------------------
# Summary one-liner
# ------------------------------------------------------------------------------

def cmd_summary(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    s = engine_stats(engine)
    print(f"Positions: {s['position_count']} (active: {s['active_count']}, sold: {s['sold_count']})")
    print(f"Orders: {s['order_count']} | Total sold wei: {s['total_sold_wei']}")


# -----------------------------------------------------------------------------
# Paths and dirs
# ------------------------------------------------------------------------------

def get_config_dir() -> Path:
    return Path.home() / OWL_CONFIG_DIR


def ensure_dirs() -> None:
    get_config_dir().mkdir(parents=True, exist_ok=True)


# -----------------------------------------------------------------------------
# Config defaults
# ------------------------------------------------------------------------------

def get_default_config() -> Dict[str, Any]:
    return {
        "guardian": OWL_DEFAULT_GUARDIAN,
        "treasury": OWL_DEFAULT_TREASURY,
        "seed_whitelist": True,
        "mock_prices": {},
    }


# -----------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------

def validate_config_keys(config: Dict[str, Any]) -> List[str]:
    required = []
    if "guardian" in config and len(config.get("guardian", "")) != 42:
        required.append("guardian must be 40 hex with 0x")
    return required


# -----------------------------------------------------------------------------
# Run single trigger and persist
# ------------------------------------------------------------------------------

def run_trigger_and_save(engine: SpringaEngine, position_id: str) -> Optional[SellOrder]:
    order = engine.check_and_trigger(position_id)
    if order:
        persist_engine(engine)
    return order


# -----------------------------------------------------------------------------
# Run scan and persist
# ------------------------------------------------------------------------------

def run_scan_and_save(engine: SpringaEngine, caller: str) -> List[SellOrder]:
    orders = engine.scan_all_positions(caller)
    if orders:
        persist_engine(engine)
    return orders


# -----------------------------------------------------------------------------
# Format position one-liner
# ------------------------------------------------------------------------------

def format_position_line(p: Position, current_price: Optional[int] = None) -> str:
    drop = ""
    if current_price is not None and p.high_water_mark_wei > 0:
        drop = f" drop={compute_drop_bps(p.high_water_mark_wei, current_price)}bps"
    return f"{p.position_id[:12]}... {p.asset_id} {status_display(p.status)}{drop}"


# -----------------------------------------------------------------------------
# List positions by asset
# ------------------------------------------------------------------------------

def cmd_by_asset(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: byasset <asset_id>")
        return
    from Springa import filter_positions_by_asset
    positions = filter_positions_by_asset(engine.list_positions(), args[1])
    print(positions_table(positions, engine._price_feed))


# -----------------------------------------------------------------------------
# Counts
# ------------------------------------------------------------------------------

def cmd_counts(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    positions = engine.list_positions()
    active = sum(1 for p in positions if p.status == SPRG_STATUS_ACTIVE)
    sold = sum(1 for p in positions if p.status == SPRG_STATUS_SOLD)
    disabled = sum(1 for p in positions if p.status == 4)
    print(f"Total: {len(positions)} | Active: {active} | Sold: {sold} | Disabled: {disabled}")


# -----------------------------------------------------------------------------
# Config show path
# ------------------------------------------------------------------------------

def cmd_paths(args: List[str]) -> None:
    print("config:", get_config_path())
    print("state:", get_state_path())
    print("history:", get_history_path())
    print("dir:", get_config_dir())


# -----------------------------------------------------------------------------
# Mock prices from config
# ------------------------------------------------------------------------------

def set_mock_prices_in_config(prices: Dict[str, int]) -> None:
    config = load_config()
    config["mock_prices"] = prices
    save_config(config)


def cmd_setprices(args: List[str]) -> None:
    if len(args) < 3:
        print("Usage: setprices <asset_id> <price_wei> [asset_id2 price2 ...]")
        return
    config = load_config()
    prices = dict(config.get("mock_prices", {}))
    i = 1
    while i + 1 <= len(args):
        prices[args[i]] = parse_wei(args[i + 1])
        i += 2
    config["mock_prices"] = prices
    save_config(config)
    print("Mock prices updated.")


# -----------------------------------------------------------------------------
# Guardian / keeper check
# ------------------------------------------------------------------------------

def cmd_whoami(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    addr = to_checksum_address(args[1]) if len(args) > 1 else None
    if not addr:
        print("Usage: whoami <address>")
        return
    try:
        from Springa import is_guardian, is_keeper
        print("guardian:", is_guardian(engine, addr))
        print("keeper:", is_keeper(engine, addr))
    except ImportError:
        print("is_guardian/is_keeper not available.")


# -----------------------------------------------------------------------------
# Position IDs for owner
# ------------------------------------------------------------------------------

def cmd_mypositions(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: mypositions <owner_address>")
        return
    owner = to_checksum_address(args[1])
    positions = engine.list_positions(owner=owner)
    for p in positions:
        print(p.position_id)


# -----------------------------------------------------------------------------
# Order count
# ------------------------------------------------------------------------------

def cmd_ordercount(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    position_id = args[1] if len(args) > 1 else None
    orders = engine.list_orders(position_id=position_id)
    print(len(orders))


# -----------------------------------------------------------------------------
# Table format options (stub for future)
# ------------------------------------------------------------------------------

def table_format_positions(positions: List[Position], feed: Optional[Any] = None, style: str = "table") -> str:
    if style == "csv":
        lines = ["position_id,owner,asset_id,amount_wei,status"]
        for p in positions:
            lines.append(f"{p.position_id},{p.owner},{p.asset_id},{p.amount_wei},{status_display(p.status)}")
        return "\n".join(lines)
    return positions_table(positions, feed)


# -----------------------------------------------------------------------------
# Export state path
# ------------------------------------------------------------------------------

def default_export_path() -> Path:
    return get_config_dir() / f"export_{int(time.time())}.json"


# -----------------------------------------------------------------------------
# Load state from path
# ------------------------------------------------------------------------------

def load_state_from_path(engine: SpringaEngine, path: Path) -> None:
    if not path.exists():
        return
    with open(path) as f:
        engine.load_state(json.load(f))


# -----------------------------------------------------------------------------
# Backup state
# ------------------------------------------------------------------------------

def cmd_backup(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    path = Path(args[1]) if len(args) > 1 else default_export_path()
    save_engine_state(engine, path)
    print(f"Backup: {path}")


# -----------------------------------------------------------------------------
# Restore state
# ------------------------------------------------------------------------------

def cmd_restore(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: restore <path>")
        return
    load_engine_state(engine, args[1])
    persist_engine(engine)
    print("Restored.")


# -----------------------------------------------------------------------------
# Version info
# ------------------------------------------------------------------------------

def get_owl_version() -> str:
    return OWL_VERSION


def get_springa_version() -> str:
    try:
        from Springa import SPRG_VERSION
        return SPRG_VERSION
    except ImportError:
        return "N/A"


# -----------------------------------------------------------------------------
# Health summary one-liner
# ------------------------------------------------------------------------------

def cmd_health_short(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    h = engine_health(engine)
    print("OK" if h.get("ok") else "Errors: " + str(h.get("errors", [])))


# -----------------------------------------------------------------------------
# List assets in whitelist
# ------------------------------------------------------------------------------

def cmd_assets(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    wl = list(engine._whitelist)
    if not wl:
        print("(no whitelist or empty)")
        return
    for a in sorted(wl):
        print(a)


# -----------------------------------------------------------------------------
# Position status only
# ------------------------------------------------------------------------------

def cmd_status(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: status <position_id>")
        return
    pos = engine.get_position(args[1])
    if not pos:
        print("Not found.")
        return
    print(status_display(pos.status))


# -----------------------------------------------------------------------------
# JSON export of single position
# ------------------------------------------------------------------------------

def cmd_position_json(args: List[str], engine: Optional[SpringaEngine]) -> None:
    if not engine:
        print("Springa not available.")
        return
    if len(args) < 2:
        print("Usage: positionjson <position_id>")
        return
    pos = engine.get_position(args[1])
    if not pos:
        print("Not found.")
        return
    report = position_report(pos, engine._price_feed)
    print(json.dumps(report, indent=2))


# -----------------------------------------------------------------------------
# Preset info
# ------------------------------------------------------------------------------

def cmd_presets(args: List[str]) -> None:
    print("conservative: drop_bps=1000, floor_bps=800")
    print("moderate: drop_bps=2000, floor_bps=500")
    print("aggressive: drop_bps=3500, floor_bps=300")


# -----------------------------------------------------------------------------
# Config dump
# ------------------------------------------------------------------------------


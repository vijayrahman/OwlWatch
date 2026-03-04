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

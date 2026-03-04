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



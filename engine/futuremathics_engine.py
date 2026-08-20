"""Standalone FutureMathicsEngine sketch — mock-tick / demo path (not the live virtue loop)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from engine import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Keep demo state off live Streamlit path (Justice — never clobber production state).
_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "engine_sketch_state.json"


class FutureMathicsEngine:
    def __init__(self) -> None:
        self.state: Dict[str, Any] = {
            "position": "FLAT",
            "entry_price": 0.0,
            "daily_pnl": 0.0,
            "cooldown_cycles": 0,
            "vwap_score": 50.0,
            "twap_score": 50.0,
            "atr": 0.0,
        }
        self.long_streak = 0
        self.short_streak = 0
        self.is_running = True

    async def load_persisted_state(self) -> None:
        try:
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for k in ["position", "entry_price", "daily_pnl"]:
                    if k in saved:
                        self.state[k] = saved[k]
                logging.info("State recovered: %s", self.state["position"])
        except FileNotFoundError:
            logging.warning("No sketch state file found. Starting fresh.")
        except Exception as exc:
            logging.error("Justice Layer load failure: %s", exc)

    async def save_state_throttled(self) -> None:
        while self.is_running:
            try:
                await asyncio.to_thread(self._write_state_to_disk)
            except Exception as e:
                logging.error("Justice Layer write failure: %s", e)
            await asyncio.sleep(float(getattr(config, "VIRTUE_STATE_PERSIST_INTERVAL_S", 2.0)))

    def _write_state_to_disk(self) -> None:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def calculate_targets(self, entry_price: float, direction: str, atr: float) -> Tuple[float, float]:
        stop_ticks = 60
        tp_ticks = max(120, int(round(atr * 4 * float(config.VIRTUE_TP_ATR_MULT))))
        tick_offset = float(config.TICK_SIZE)

        if direction == "LONG":
            stop_loss = entry_price - (stop_ticks * tick_offset)
            take_profit = entry_price + (tp_ticks * tick_offset)
        else:
            stop_loss = entry_price + (stop_ticks * tick_offset)
            take_profit = entry_price - (tp_ticks * tick_offset)
        return stop_loss, take_profit

    async def process_market_tick(self, current_price: float, vwap: float, twap: float, atr: float) -> None:
        self.state["atr"] = float(atr)
        if self.state["daily_pnl"] <= -config.MAX_DAILY_LOSS or self.state["daily_pnl"] >= config.PROFIT_LOCK:
            if self.state["position"] != "FLAT":
                await self.execute_order("FLATTEN", current_price)
            return

        self.state["vwap_score"] = max(
            0.0, min(100.0, 50.0 + ((current_price - vwap) / (atr + 0.01) * 10))
        )
        self.state["twap_score"] = max(
            0.0, min(100.0, 50.0 + ((current_price - twap) / (atr + 0.01) * 10))
        )

        if abs(vwap - twap) > (atr * float(config.VIRTUE_ANCHOR_DIVERGENCE_ATR_MULT)) and self.state[
            "cooldown_cycles"
        ] == 0:
            logging.info("Anchor rebase triggered. Activating 2-cycle cooldown.")
            self.state["cooldown_cycles"] = int(config.VIRTUE_POST_REBASE_ENTRY_COOLDOWN_CYCLES)
            return

        if self.state["cooldown_cycles"] > 0:
            self.state["cooldown_cycles"] -= 1
            return

        v_score = self.state["vwap_score"]
        is_raw_long = v_score >= config.LONG_ENTER
        is_raw_short = v_score <= config.SHORT_ENTER

        self.long_streak = (self.long_streak + 1) if is_raw_long else 0
        self.short_streak = (self.short_streak + 1) if is_raw_short else 0

        current_pos = self.state["position"]

        if current_pos == "FLAT":
            if self.long_streak >= config.REQUIRED_STREAK:
                await self.execute_order("LONG", current_price)
            elif self.short_streak >= config.REQUIRED_STREAK:
                await self.execute_order("SHORT", current_price)

        elif current_pos == "LONG":
            stop, tp = self.calculate_targets(self.state["entry_price"], "LONG", atr)
            if current_price <= stop or current_price >= tp or (
                v_score <= config.LONG_EXIT
            ):
                await self.execute_order("FLATTEN", current_price)
                if self.short_streak >= config.REQUIRED_STREAK:
                    await self.execute_order("SHORT", current_price)

        elif current_pos == "SHORT":
            stop, tp = self.calculate_targets(self.state["entry_price"], "SHORT", atr)
            if current_price >= stop or current_price <= tp or (
                v_score >= config.SHORT_EXIT
            ):
                await self.execute_order("FLATTEN", current_price)
                if self.long_streak >= config.REQUIRED_STREAK:
                    await self.execute_order("LONG", current_price)

    async def execute_order(self, action: str, price: float) -> None:
        if action == "FLATTEN":
            pnl_points = (
                (price - self.state["entry_price"])
                if self.state["position"] == "LONG"
                else (self.state["entry_price"] - price)
            )
            trade_pnl = pnl_points * 4 * config.TICK_VALUE
            self.state["daily_pnl"] += trade_pnl
            logging.info(
                "FLATTEN executed at %s. Trade PnL: $%.2f. Total Daily PnL: $%.2f",
                price,
                trade_pnl,
                self.state["daily_pnl"],
            )
            self.state["position"] = "FLAT"
            self.state["entry_price"] = 0.0
        else:
            self.state["position"] = action
            self.state["entry_price"] = price
            logging.info("Entry executed: %s at %s. Noise-reduction cleared.", action, price)

#!/usr/bin/env python3
"""
Live trade execution module.
"""

from __future__ import annotations

from binance_api import BinanceClient
from risk_guard import RiskGuard, RiskState
from telegram_notifier import send_message


class TradeExecutor:
    def __init__(self):
        self.client = BinanceClient()

    def execute_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        risk_state: RiskState,
    ):
        guard = RiskGuard(risk_state)
        allowed, reason = guard.can_trade()

        if not allowed:
            send_message(f"❌ Trade blocked: {reason}")
            return {
                "status": "blocked",
                "reason": reason,
            }

        order = self.client.create_market_order(symbol, side, amount)

        send_message(
            f"✅ Market order executed\n"
            f"Symbol: {symbol}\n"
            f"Side: {side}\n"
            f"Amount: {amount}"
        )

        return {
            "status": "executed",
            "order": order,
        }

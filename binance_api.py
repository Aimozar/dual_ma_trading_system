#!/usr/bin/env python3
"""
Minimal Binance exchange wrapper using ccxt.
Phase 2 live trading module.
"""

from __future__ import annotations

import os
from typing import Any, Dict

import ccxt
from dotenv import load_dotenv

load_dotenv()


class BinanceClient:
    def __init__(self, sandbox: bool = False):
        self.exchange = ccxt.binance({
            "apiKey": os.getenv("BINANCE_API_KEY"),
            "secret": os.getenv("BINANCE_API_SECRET"),
            "enableRateLimit": True,
            "options": {
                "defaultType": "future"
            }
        })

        if sandbox:
            self.exchange.set_sandbox_mode(True)

    def fetch_balance(self) -> Dict[str, Any]:
        return self.exchange.fetch_balance()

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def fetch_ohlcv(self, symbol: str, timeframe: str = "4h", limit: int = 200):
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def create_market_order(self, symbol: str, side: str, amount: float):
        return self.exchange.create_market_order(symbol, side, amount)

    def create_limit_order(self, symbol: str, side: str, amount: float, price: float):
        return self.exchange.create_limit_order(symbol, side, amount, price)

    def close(self):
        try:
            self.exchange.close()
        except Exception:
            pass

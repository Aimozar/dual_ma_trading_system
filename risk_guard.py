#!/usr/bin/env python3
"""
Risk guard / circuit breaker module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskState:
    daily_loss_pct: float
    consecutive_losses: int
    funding_rate: float
    max_daily_loss_pct: float = 5.0
    max_consecutive_losses: int = 3
    max_funding_rate: float = 0.05


class RiskGuard:
    def __init__(self, state: RiskState):
        self.state = state

    def can_trade(self) -> tuple[bool, str]:
        if self.state.daily_loss_pct >= self.state.max_daily_loss_pct:
            return False, "Daily loss limit reached"

        if self.state.consecutive_losses >= self.state.max_consecutive_losses:
            return False, "Too many consecutive losses"

        if abs(self.state.funding_rate) >= self.state.max_funding_rate:
            return False, "Funding rate too high"

        return True, "OK"

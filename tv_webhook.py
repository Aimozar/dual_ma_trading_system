#!/usr/bin/env python3
"""
TradingView webhook receiver.
"""

from __future__ import annotations

from flask import Flask, request, jsonify

from risk_guard import RiskState
from trade_executor import TradeExecutor

app = Flask(__name__)
executor = TradeExecutor()


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json

    symbol = data.get('symbol')
    side = data.get('side')
    amount = float(data.get('amount', 0))

    risk_state = RiskState(
        daily_loss_pct=0,
        consecutive_losses=0,
        funding_rate=0.01,
    )

    result = executor.execute_market_order(
        symbol=symbol,
        side=side,
        amount=amount,
        risk_state=risk_state,
    )

    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

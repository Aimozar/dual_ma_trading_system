#!/usr/bin/env python3
"""
Trade logger for the dual MA trading system.

Creates and appends standardized trade records to logs/trades.csv.
This module is intentionally lightweight so it can be called by strategy,
execution, or manual review scripts.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_LOG_PATH = Path("logs/trades.csv")


@dataclass
class TradeRecord:
    trade_id: str
    opened_at: str
    closed_at: str
    symbol: str
    timeframe: str
    direction: str
    entry: float
    stop_loss: float
    exit_price: float
    position_size_usdt: float
    pnl_usdt: float
    pnl_pct: float
    rr: float
    entry_strategy: str
    exit_reason: str
    score: float
    bias_mode: str
    notes: str = ""


FIELDS = list(TradeRecord.__dataclass_fields__.keys())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_log_file(path: Path = DEFAULT_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()


def append_trade(record: TradeRecord, path: Path = DEFAULT_LOG_PATH) -> None:
    ensure_log_file(path)
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writerow(asdict(record))


def build_trade_id(symbol: str, timeframe: str, opened_at: Optional[str] = None) -> str:
    ts = opened_at or utc_now()
    safe_symbol = symbol.replace("/", "")
    safe_ts = ts.replace(":", "").replace("-", "").replace(".", "")[:20]
    return f"{safe_symbol}-{timeframe}-{safe_ts}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append a standardized trade record.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--direction", required=True, choices=["LONG", "SHORT"])
    parser.add_argument("--entry", required=True, type=float)
    parser.add_argument("--stop-loss", required=True, type=float)
    parser.add_argument("--exit-price", required=True, type=float)
    parser.add_argument("--position-size-usdt", required=True, type=float)
    parser.add_argument("--pnl-usdt", required=True, type=float)
    parser.add_argument("--pnl-pct", required=True, type=float)
    parser.add_argument("--rr", required=True, type=float)
    parser.add_argument("--entry-strategy", default="UNKNOWN")
    parser.add_argument("--exit-reason", default="UNKNOWN")
    parser.add_argument("--score", default=0.0, type=float)
    parser.add_argument("--bias-mode", default="neutral")
    parser.add_argument("--notes", default="")
    parser.add_argument("--opened-at", default=None)
    parser.add_argument("--closed-at", default=None)
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    opened_at = args.opened_at or utc_now()
    closed_at = args.closed_at or utc_now()
    record = TradeRecord(
        trade_id=build_trade_id(args.symbol, args.timeframe, opened_at),
        opened_at=opened_at,
        closed_at=closed_at,
        symbol=args.symbol,
        timeframe=args.timeframe,
        direction=args.direction,
        entry=args.entry,
        stop_loss=args.stop_loss,
        exit_price=args.exit_price,
        position_size_usdt=args.position_size_usdt,
        pnl_usdt=args.pnl_usdt,
        pnl_pct=args.pnl_pct,
        rr=args.rr,
        entry_strategy=args.entry_strategy,
        exit_reason=args.exit_reason,
        score=args.score,
        bias_mode=args.bias_mode,
        notes=args.notes,
    )
    append_trade(record, Path(args.log_path))
    print(f"Trade logged: {record.trade_id}")


if __name__ == "__main__":
    main()

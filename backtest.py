#!/usr/bin/env python3
"""
Backtest engine for the dual MA trading system.

Input CSV trade log columns:
- opened_at, closed_at, symbol, timeframe, direction
- entry, stop_loss, exit_price
- position_size_usdt, pnl_usdt, pnl_pct, rr, exit_reason
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any

import pandas as pd

CONFIG_PATH = Path("config.json")
REPORT_DIR = Path("reports")


def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_trades(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    required = {"pnl_pct", "pnl_usdt", "rr"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    return df


def build_equity_curve(df: pd.DataFrame, initial_equity: float) -> pd.DataFrame:
    equity = initial_equity
    rows = []
    peak = initial_equity

    for idx, row in df.reset_index(drop=True).iterrows():
        pnl = float(row.get("pnl_usdt", 0.0))
        equity += pnl
        peak = max(peak, equity)
        drawdown_pct = (equity - peak) / peak if peak else 0.0
        rows.append({
            "trade_index": idx + 1,
            "equity": equity,
            "peak": peak,
            "drawdown_pct": drawdown_pct,
        })

    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, initial_equity: float) -> Dict[str, Any]:
    wins = df[df["pnl_usdt"] > 0]
    losses = df[df["pnl_usdt"] < 0]
    gross_profit = float(wins["pnl_usdt"].sum())
    gross_loss = abs(float(losses["pnl_usdt"].sum()))
    equity_curve = build_equity_curve(df, initial_equity)

    total_pnl = float(df["pnl_usdt"].sum())
    total_return_pct = total_pnl / initial_equity * 100 if initial_equity else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss else None
    max_drawdown_pct = float(equity_curve["drawdown_pct"].min() * 100) if not equity_curve.empty else 0.0

    return {
        "total_trades": int(len(df)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_pct": float((len(wins) / len(df) * 100) if len(df) else 0.0),
        "avg_rr": float(df["rr"].mean()) if len(df) else 0.0,
        "median_rr": float(df["rr"].median()) if len(df) else 0.0,
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": profit_factor,
        "total_pnl_usdt": total_pnl,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "final_equity": float(initial_equity + total_pnl),
    }


def write_report(summary: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Backtest Report",
        "",
        f"- Total trades: {summary['total_trades']}",
        f"- Win rate: {summary['win_rate_pct']:.2f}%",
        f"- Average RR: {summary['avg_rr']:.2f}",
        f"- Median RR: {summary['median_rr']:.2f}",
        f"- Profit factor: {summary['profit_factor'] if summary['profit_factor'] is not None else 'N/A'}",
        f"- Total PnL: {summary['total_pnl_usdt']:.2f} USDT",
        f"- Total return: {summary['total_return_pct']:.2f}%",
        f"- Max drawdown: {summary['max_drawdown_pct']:.2f}%",
        f"- Final equity: {summary['final_equity']:.2f} USDT",
        "",
        "> This report is for strategy research only and is not investment advice.",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def run_backtest(csv_path: str, output: str | None = None) -> Dict[str, Any]:
    config = load_config()
    initial_equity = float(config.get("initial_equity", 10000))
    df = load_trades(csv_path)
    summary = summarize(df, initial_equity)

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if output:
        write_report(summary, Path(output))

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trade-log based backtest summary.")
    parser.add_argument("--csv", required=True, help="Path to trade log CSV")
    parser.add_argument("--output", default="reports/backtest_report.md", help="Markdown report output path")
    args = parser.parse_args()
    run_backtest(args.csv, args.output)


if __name__ == "__main__":
    main()

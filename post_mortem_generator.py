#!/usr/bin/env python3
"""
Post-mortem report generator.

Generates structured markdown reviews for losing or problematic trades.
Used to enforce the rule:
"No new trade before previous loss has been reviewed."
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

REPORT_DIR = Path("reports/post_mortems")


def load_trade(csv_path: str, trade_id: str) -> pd.Series:
    df = pd.read_csv(csv_path)
    rows = df[df["trade_id"] == trade_id]
    if rows.empty:
        raise ValueError(f"Trade not found: {trade_id}")
    return rows.iloc[0]


def classify_issue(row: pd.Series) -> list[str]:
    issues = []

    if float(row.get("rr", 0)) < 3:
        issues.append("RR below hard-rule threshold (<3.0)")

    if float(row.get("pnl_pct", 0)) < -0.02:
        issues.append("Loss exceeded 2% account risk")

    exit_reason = str(row.get("exit_reason", "")).lower()
    if "fomo" in exit_reason:
        issues.append("Possible emotional/FOMO entry")

    if "late" in exit_reason:
        issues.append("Late entry / trend exhaustion")

    if not issues:
        issues.append("No major structural issue detected")

    return issues


def build_report(row: pd.Series) -> str:
    issues = classify_issue(row)

    report = []
    report.append(f"# Post Mortem — {row['trade_id']}")
    report.append("")
    report.append(f"Generated: {datetime.utcnow().isoformat()} UTC")
    report.append("")
    report.append("## Trade Summary")
    report.append("")
    report.append(f"- Symbol: {row['symbol']}")
    report.append(f"- Timeframe: {row['timeframe']}")
    report.append(f"- Direction: {row['direction']}")
    report.append(f"- Entry: {row['entry']}")
    report.append(f"- Exit: {row['exit_price']}")
    report.append(f"- RR: {row['rr']}")
    report.append(f"- PnL: {row['pnl_usdt']} USDT")
    report.append(f"- PnL %: {row['pnl_pct']}%")
    report.append("")
    report.append("## Potential Issues")
    report.append("")

    for issue in issues:
        report.append(f"- {issue}")

    report.append("")
    report.append("## Reflection Questions")
    report.append("")
    report.append("- Was the trade aligned with HTF trend?")
    report.append("- Did the setup satisfy minimum RR >= 3?")
    report.append("- Was there any emotional interference?")
    report.append("- Was position sizing within hard risk limits?")
    report.append("- Did BTC market structure support the trade?")
    report.append("")
    report.append("## Improvement Actions")
    report.append("")
    report.append("- Reduce impulsive entries.")
    report.append("- Wait for stronger confirmation.")
    report.append("- Respect stop-loss rules.")

    return "\n".join(report)


def save_report(trade_id: str, content: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{trade_id}.md"
    path.write_text(content, encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a trade post mortem report.")
    parser.add_argument("--csv", default="logs/trades.csv")
    parser.add_argument("--trade-id", required=True)
    args = parser.parse_args()

    row = load_trade(args.csv, args.trade_id)
    content = build_report(row)
    path = save_report(args.trade_id, content)

    print(f"Post mortem generated: {path}")


if __name__ == "__main__":
    main()

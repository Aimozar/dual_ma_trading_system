#!/usr/bin/env python3
"""
Simple backtest engine for dual MA trading system.
Phase 1 foundation module.
"""

import json
from pathlib import Path

import pandas as pd

CONFIG_PATH = 'config.json'


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_csv(path: str):
    df = pd.read_csv(path)
    return df


def calculate_equity_curve(df: pd.DataFrame):
    equity = [1.0]
    for pnl in df['pnl_pct']:
        equity.append(equity[-1] * (1 + pnl))
    return equity


def run_backtest(csv_path: str):
    config = load_config()
    df = load_csv(csv_path)

    total_trades = len(df)
    win_rate = (df['pnl_pct'] > 0).mean() * 100
    avg_rr = df['rr'].mean()
    total_return = df['pnl_pct'].sum() * 100

    print('===== Backtest Summary =====')
    print(f'Symbol: {config["symbol"]}')
    print(f'Timeframe: {config["timeframe"]}')
    print(f'Total Trades: {total_trades}')
    print(f'Win Rate: {win_rate:.2f}%')
    print(f'Average RR: {avg_rr:.2f}')
    print(f'Total Return: {total_return:.2f}%')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', required=True)
    args = parser.parse_args()

    run_backtest(args.csv)

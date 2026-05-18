#!/usr/bin/env python3
""" 
双均线/三均线交易系统 v3.1 — 生产级 EMA20/60/120 趋势跟踪 + 多维信号确认 + 完整风控 
"""

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════
# 1. 常量与配置
# ═══════════════════════════════════════════════
VERSION = "3.1.0"

FEE_RATES = {
    "okx": {"spot": 0.00100, "futures_maker": 0.00020, "futures_taker": 0.00050},
    "bybit": {"spot": 0.00100, "futures_maker": 0.00020, "futures_taker": 0.00055},
    "binance": {"spot": 0.00100, "futures_maker": 0.00020, "futures_taker": 0.00050},
    "bitget": {"spot": 0.00100, "futures_maker": 0.00020, "futures_taker": 0.00060},
    "hyperliquid": {"spot": 0.00070, "futures_maker": 0.00000, "futures_taker": 0.00045},
}

TIMEFRAME_HIERARCHY = {
    "5m": ["15m", "1h"],
    "15m": ["1h", "4h"],
    "1h": ["4h", "1d"],
    "4h": ["1d", "1w"],
    "1d": ["1w"],
    "1w": [],
}

TIMEFRAME_MINUTES = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1w": 10080,
}

DEFAULT_CONFIG = {
    "ma_periods": [20, 60, 120],
    "atr_period": 14,
    "atr_stop_multiplier": 1.5,
    "rsi_period": 14,
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "adx_period": 14,
    "adx_strong_trend": 25,
    "bb_period": 20,
    "bb_std": 2.0,
    "risk_per_trade": 0.02,
    "max_account_risk": 0.06,
    "max_drawdown_pct": 0.15,
    "signal_cooldown_bars": 3,
    "data_staleness_minutes": 30,
    "min_candles_required": 150,
    "partial_tp_ratios": [0.5, 0.3, 0.2],
    "partial_tp_rr": [1.0, 2.0, 3.0],
    "trailing_stop_activation_rr": 1.5,
    "trailing_stop_callback": 0.5,
    "breakeven_activation_rr": 1.0,
    "exchanges_priority": ["okx", "bybit", "binance"],
    "retry_max": 3,
    "retry_delay_sec": 2,
    "trailing_atr_mult": 3.0,
    "trailing_lookback": 22,
    "swing_lookback": 20,
    "swing_fractal_n": 2,
    "swing_buffer_atr": 0.3,
    "funding_rate_daily_estimate": 0.0003,
    "btc_align_enabled": True,
    "htf_veto_confidence": 50,
}

CACHE_DIR = Path(__file__).parent.parent / ".cache"
logger = logging.getLogger("dual_ma")

def _smart_round(value, ref_price):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if ref_price is None or ref_price == 0:
        return round(value, 2)
    
    mag = math.floor(math.log10(abs(ref_price))) if ref_price != 0 else 0
    if mag >= 3:
        return round(value, 2)
    elif mag >= 0:
        return round(value, 4)
    else:
        return round(value, max(6, -mag + 4))

# ═══════════════════════════════════════════════
# 2. 配置管理
# ═══════════════════════════════════════════════
def load_config(config_path=None):
    cfg = dict(DEFAULT_CONFIG)
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            if config_path.endswith(".json"):
                user_cfg = json.load(f)
            else:
                try:
                    import yaml
                    user_cfg = yaml.safe_load(f)
                except ImportError:
                    logger.warning("PyYAML 未安装，跳过 YAML 配置")
                    user_cfg = {}
            cfg.update(user_cfg)
    return cfg

# ═══════════════════════════════════════════════
# 3. 数据缓存
# ═══════════════════════════════════════════════
def _cache_key(symbol, timeframe):
    raw = f"{symbol}:{timeframe}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def _read_cache(symbol, timeframe, max_age_sec=300):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(symbol, timeframe)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = data.get("cached_at", 0)
        if time.time() - cached_at > max_age_sec:
            return None
        return data.get("ohlcv")
    except Exception:
        return None

def _write_cache(symbol, timeframe, ohlcv):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(symbol, timeframe)}.json"
    payload = {
        "cached_at": time.time(), 
        "symbol": symbol, 
        "timeframe": timeframe, 
        "ohlcv": ohlcv
    }
    try:
        path.write_text(json.dumps(payload))
    except Exception:
        pass

# ═══════════════════════════════════════════════
# 4. 数据获取（带重试、降级、校验）
# ═══════════════════════════════════════════════
def _create_exchange(exchange_id):
    try:
        import ccxt
    except ImportError:
        logger.error("ccxt 未安装: pip install ccxt")
        return None
    cls = getattr(ccxt, exchange_id, None)
    if cls is None:
        return None
    return cls({"enableRateLimit": True, "timeout": 15000})

def _validate_ohlcv(ohlcv, symbol, timeframe, cfg):
    if not ohlcv:
        return False, "空数据"
    if len(ohlcv) < cfg["min_candles_required"]:
        return False, f"K线不足: {len(ohlcv)}/{cfg['min_candles_required']}"
    
    last_ts = ohlcv[-1][0] / 1000
    now = time.time()
    tf_min = TIMEFRAME_MINUTES.get(timeframe, 60)
    staleness = (now - last_ts) / 60
    max_staleness = tf_min * 2 + cfg["data_staleness_minutes"]
    
    if staleness > max_staleness:
        return False, f"数据陈旧: 最后K线距今 {staleness:.0f} 分钟"
        
    for i, candle in enumerate(ohlcv):
        if candle[2] < candle[3]:  # high < low
            return False, f"异常K线 #{i}: high({candle[2]}) < low({candle[3]})"
        if any(v is None or v < 0 for v in candle[1:5]):
            return False, f"无效价格数据 #{i}"
            
    timestamps = [c[0] for c in ohlcv]
    gaps = 0
    expected_gap = tf_min * 60 * 1000
    for i in range(1, len(timestamps)):
        actual_gap = timestamps[i] - timestamps[i - 1]
        if actual_gap > expected_gap * 2.5:
            gaps += 1
            
    if gaps > len(ohlcv) * 0.1:
        return False, f"K线缺口过多: {gaps} 处"
        
    return True, "OK"

def fetch_market_data(symbol, timeframe, cfg):
    cache_age = TIMEFRAME_MINUTES.get(timeframe, 60) * 60
    cached = _read_cache(symbol, timeframe, max_age_sec=min(cache_age, 300))
    if cached:
        logger.info(f"使用缓存数据 ({symbol} {timeframe})")
        return _build_dataframe(cached, cfg, timeframe)
        
    exchanges = cfg.get("exchanges_priority", ["okx", "bybit", "binance"])
    retry_max = cfg.get("retry_max", 3)
    retry_delay = cfg.get("retry_delay_sec", 2)
    limit = cfg["ma_periods"][2] * 3
    errors = []
    
    for exchange_id in exchanges:
        for attempt in range(1, retry_max + 1):
            try:
                exchange = _create_exchange(exchange_id)
                if exchange is None:
                    errors.append(f"{exchange_id}: 不支持的交易所")
                    break
                    
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                valid, msg = _validate_ohlcv(ohlcv, symbol, timeframe, cfg)
                if not valid:
                    errors.append(f"{exchange_id}: {msg}")
                    break
                    
                _write_cache(symbol, timeframe, ohlcv)
                df, meta = _build_dataframe(ohlcv, cfg, timeframe)
                meta["source"] = exchange_id
                meta["fetched_at"] = datetime.now(timezone.utc).isoformat()
                return df, meta
            except Exception as e:
                errors.append(f"{exchange_id}(attempt {attempt}): {e}")
                if attempt < retry_max:
                    time.sleep(retry_delay * attempt)
                    continue
                    
        logger.error("所有数据源失败:\n" + "\n".join(errors))
        
    return None, None

def _build_dataframe(ohlcv, cfg, timeframe=None):
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    p = cfg["ma_periods"]
    
    df["ma_fast"] = df["close"].ewm(span=p[0], adjust=False).mean()
    df["ma_slow"] = df["close"].ewm(span=p[1], adjust=False).mean()
    df["ma_ultra"] = df["close"].ewm(span=p[2], adjust=False).mean()
    
    _calc_atr(df, cfg["atr_period"])
    _calc_rsi(df, cfg["rsi_period"])
    _calc_macd(df)
    _calc_adx(df, cfg["adx_period"])
    _calc_bollinger(df, cfg["bb_period"], cfg["bb_std"])
    _calc_vwap(df, timeframe=timeframe)
    _calc_ma_slope(df)
    _calc_volume_profile(df)
    _detect_candle_patterns(df)
    _calc_support_resistance(df)
    
    latest = df.iloc[-1]
    meta = {
        "current_price": float(latest["close"]),
        "ma20": float(latest["ma_fast"]),
        "ma60": float(latest["ma_slow"]),
        "ma120": float(latest["ma_ultra"]),
    }
    return df, meta

# ═══════════════════════════════════════════════
# 5. 技术指标计算
# ═══════════════════════════════════════════════
def _calc_atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["tr"] = tr
    df["atr"] = tr.ewm(alpha=1.0 / period, adjust=False).mean()

def _calc_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

def _calc_macd(df, fast=12, slow=26, signal=9):
    ema_f = df["close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_f - ema_s
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

def _calc_adx(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    plus_dm = (h - h.shift(1)).clip(lower=0)
    minus_dm = (l.shift(1) - l).clip(lower=0)
    
    # 必须基于原始值做互斥判断，否则第二次比较会用到被清零后的 plus_dm
    plus_dm_orig = plus_dm.copy()
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm_orig] = 0
    
    atr = df["atr"] if "atr" in df.columns else (h - l).rolling(period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr.replace(0, np.nan))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df["adx"] = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

def _calc_bollinger(df, period=20, std_mult=2.0):
    sma = df["close"].rolling(window=period).mean()
    std = df["close"].rolling(window=period).std()
    df["bb_upper"] = sma + std * std_mult
    df["bb_middle"] = sma
    df["bb_lower"] = sma - std * std_mult
    df["bb_width"] = ((df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]).fillna(0)
    df["bb_position"] = ((df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)).fillna(0.5)

def _calc_vwap(df, timeframe=None):
    typical = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = typical * df["volume"]
    # 日内周期按 UTC 自然日重置；4h/1d/1w 不重置（连续累计意义不大但保持向后兼容）
    intraday = timeframe in ("5m", "15m", "1h")
    if intraday and "timestamp" in df.columns:
        day_key = df["timestamp"].dt.floor("D")
        cum_tp_vol = tp_vol.groupby(day_key).cumsum()
        cum_vol = df["volume"].groupby(day_key).cumsum()
    else:
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = df["volume"].cumsum()
    df["vwap"] = cum_tp_vol / cum_vol.replace(0, np.nan)

def _calc_ma_slope(df, lookback=5):
    for col, name in [("ma_fast", "ma_fast_slope"), ("ma_slow", "ma_slow_slope"), ("ma_ultra", "ma_ultra_slope")]:
        prev = df[col].shift(lookback)
        df[name] = ((df[col] - prev) / prev * 100).fillna(0)

def _calc_volume_profile(df, period=20):
    df["vol_sma"] = df["volume"].rolling(window=period).mean()
    df["vol_ratio"] = (df["volume"] / df["vol_sma"].replace(0, np.nan)).fillna(1.0)

def _detect_candle_patterns(df):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = abs(c - o)
    full_range = (h - l).replace(0, np.nan)
    body_ratio = body / full_range
    prev_c = c.shift(1)
    prev_o = o.shift(1)
    prev_body = abs(prev_c - prev_o)
    lower_shadow = pd.concat([o, c], axis=1).min(axis=1) - l
    
    hammer = (lower_shadow > body * 2) & (body_ratio < 0.4) & (c > o)
    bull_engulf = (c > prev_o) & (o < prev_c) & (body > prev_body * 1.1) & (c > o) & (prev_c < prev_o)
    bear_engulf = (c < prev_o) & (o > prev_c) & (body > prev_body * 1.1) & (c < o) & (prev_c > prev_o)
    doji = body_ratio < 0.1
    
    # 优先级: Engulfing > Hammer > Doji（只在空槽写入）
    patterns = pd.Series("", index=df.index)
    patterns = patterns.mask(bull_engulf, "BULL_ENGULF")
    patterns = patterns.mask(bear_engulf & (patterns == ""), "BEAR_ENGULF")
    patterns = patterns.mask(hammer & (patterns == ""), "HAMMER")
    patterns = patterns.mask(doji & (patterns == ""), "DOJI")
    df["candle_pattern"] = patterns

def _calc_support_resistance(df, lookback=50, touch_threshold=0.005):
    if len(df) < lookback:
        df["nearest_support"] = np.nan
        df["nearest_resistance"] = np.nan
        return
        
    recent = df.tail(lookback)
    highs = recent["high"].values
    lows = recent["low"].values
    close = float(df["close"].iloc[-1])
    levels = []
    
    all_prices = np.concatenate([highs, lows])
    all_prices.sort()
    
    i = 0
    while i < len(all_prices):
        cluster = [all_prices[i]]
        j = i + 1
        while j < len(all_prices) and (all_prices[j] - all_prices[i]) / all_prices[i] < touch_threshold:
            cluster.append(all_prices[j])
            j += 1
        if len(cluster) >= 3:
            levels.append(np.mean(cluster))
        i = j
        
    supports = [lv for lv in levels if lv < close]
    resistances = [lv for lv in levels if lv > close]
    
    df["nearest_support"] = max(supports) if supports else np.nan
    df["nearest_resistance"] = min(resistances) if resistances else np.nan

def detect_divergence(df, direction, lookback=20):
    """
    检测最近 lookback 根 K 线内的 RSI/MACD 与价格背离。
    返回: ("BEARISH"|"BULLISH"|"NONE", reason)
    - 多头方向关心顶背离 (BEARISH): 价格接近/创出新高,但 RSI 或 MACD 柱走弱
    - 空头方向关心底背离 (BULLISH): 价格接近/创出新低,但 RSI 或 MACD 柱走强
    判定:近 5 根的极值与全局极值价位接近(±0.2%),但动量指标显著回落/抬升。
    RSI 与 MACD 柱任一确认即触发;两者同时确认在 reason 中标注 STRONG。
    """
    if len(df) < lookback + 2 or "rsi" not in df.columns:
        return "NONE", ""
        
    recent = df.tail(lookback)
    closes = recent["close"].values
    rsi = recent["rsi"].values
    macd_h = recent["macd_hist"].values if "macd_hist" in df.columns else None

    def _check(price_extreme_idx, recent_extreme_idx, is_bearish):
        rsi_diverge = False
        if not np.isnan(rsi[price_extreme_idx]) and not np.isnan(rsi[recent_extreme_idx]):
            if is_bearish and rsi[recent_extreme_idx] < rsi[price_extreme_idx] - 3:
                rsi_diverge = True
            elif not is_bearish and rsi[recent_extreme_idx] > rsi[price_extreme_idx] + 3:
                rsi_diverge = True
                
        macd_diverge = False
        if macd_h is not None and not np.isnan(macd_h[price_extreme_idx]) and not np.isnan(macd_h[recent_extreme_idx]):
            # 顶背离:近期 MACD 柱低于历史峰值; 底背离:近期 MACD 柱高于历史谷值
            if is_bearish and macd_h[recent_extreme_idx] < macd_h[price_extreme_idx]:
                macd_diverge = True
            elif not is_bearish and macd_h[recent_extreme_idx] > macd_h[price_extreme_idx]:
                macd_diverge = True
        return rsi_diverge, macd_diverge

    if direction == "LONG":
        price_peak_idx = int(np.argmax(closes))
        if price_peak_idx >= lookback - 3:
            return "NONE", ""
        recent_peak_idx = (lookback - 5) + int(np.argmax(closes[-5:]))
        
        # 近期高点接近全局高点(允许略低 0.2%)
        if closes[recent_peak_idx] < closes[price_peak_idx] * 0.998:
            return "NONE", ""
            
        rsi_d, macd_d = _check(price_peak_idx, recent_peak_idx, is_bearish=True)
        if rsi_d and macd_d:
            return "BEARISH", f"STRONG: 价格双顶,RSI {rsi[price_peak_idx]:.0f}→{rsi[recent_peak_idx]:.0f} 且 MACD 柱走弱"
        if rsi_d:
            return "BEARISH", f"价格双顶但 RSI 走弱 ({rsi[price_peak_idx]:.0f}→{rsi[recent_peak_idx]:.0f})"
        if macd_d:
            return "BEARISH", f"价格双顶但 MACD 柱走弱 ({macd_h[price_peak_idx]:.4f}→{macd_h[recent_peak_idx]:.4f})"
        return "NONE", ""

    if direction == "SHORT":
        price_low_idx = int(np.argmin(closes))
        if price_low_idx >= lookback - 3:
            return "NONE", ""
        recent_low_idx = (lookback - 5) + int(np.argmin(closes[-5:]))
        
        if closes[recent_low_idx] > closes[price_low_idx] * 1.002:
            return "NONE", ""
            
        rsi_d, macd_d = _check(price_low_idx, recent_low_idx, is_bearish=False)
        if rsi_d and macd_d:
            return "BULLISH", f"STRONG: 价格双底,RSI {rsi[price_low_idx]:.0f}→{rsi[recent_low_idx]:.0f} 且 MACD 柱走强"
        if rsi_d:
            return "BULLISH", f"价格双底但 RSI 走强 ({rsi[price_low_idx]:.0f}→{rsi[recent_low_idx]:.0f})"
        if macd_d:
            return "BULLISH", f"价格双底但 MACD 柱走强 ({macd_h[price_low_idx]:.4f}→{macd_h[recent_low_idx]:.4f})"
        return "NONE", ""
        
    return "NONE", ""

# ═══════════════════════════════════════════════
# 6. 多时间框架分析
# ═══════════════════════════════════════════════
def fetch_btc_alignment(symbol, timeframe, cfg):
    """
    非 BTC 标的时,fetch BTC 同周期数据并返回趋势对齐信息。
    BTC 自身分析时返回 None。
    返回 {"trend": "BULLISH|BEARISH|NEUTRAL", "confidence": int, "btc_price": float} 或 None。
    """
    # 提取 base 资产判断是否为 BTC
    base = symbol.split("/")[0].upper() if "/" in symbol else symbol.upper()
    if base in ("BTC", "WBTC", "BTCUSDT"):
        return None
        
    df, meta = fetch_market_data("BTC/USDT", timeframe, cfg)
    if df is None or meta is None:
        return {"trend": "UNKNOWN", "confidence": 0, "btc_price": None, "error": "BTC 数据获取失败"}
        
    ma_f, ma_s, ma_u = meta["ma20"], meta["ma60"], meta["ma120"]
    if ma_f > ma_s > ma_u:
        trend = "BULLISH"
    elif ma_f < ma_s < ma_u:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"
        
    latest = df.iloc[-1]
    adx = float(latest.get("adx", 0)) if "adx" in df.columns and not np.isnan(latest.get("adx", np.nan)) else 0
    slope = float(latest.get("ma_fast_slope", 0)) if "ma_fast_slope" in df.columns else 0
    confidence = min(100, int(adx * 2 + abs(slope) * 10))
    
    return {
        "trend": trend,
        "confidence": confidence,
        "btc_price": float(latest["close"]),
        "ma_fast_slope": round(slope, 2),
        "adx": round(adx, 1),
    }

def fetch_htf_trend(symbol, timeframe, cfg, depth=1):
    higher_tfs = TIMEFRAME_HIERARCHY.get(timeframe, [])
    if not higher_tfs:
        return []
        
    results = []
    for htf in higher_tfs[:depth]:
        df, meta = fetch_market_data(symbol, htf, cfg)
        if df is None or meta is None:
            results.append({"timeframe": htf, "trend": "UNKNOWN", "confidence": 0})
            continue
            
        ma_f, ma_s, ma_u = meta["ma20"], meta["ma60"], meta["ma120"]
        latest = df.iloc[-1]
        adx = float(latest.get("adx", 0)) if "adx" in df.columns and not np.isnan(latest.get("adx", np.nan)) else 0
        
        if ma_f > ma_s and ma_s > ma_u:
            trend = "BULLISH"
        elif ma_f < ma_s and ma_s < ma_u:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
            
        slope = float(latest.get("ma_fast_slope", 0))
        confidence = min(100, int(adx * 2 + abs(slope) * 10))
        
        results.append({
            "timeframe": htf,
            "trend": trend,
            "adx": round(adx, 1),
            "ma_fast_slope": round(slope, 2),
            "confidence": confidence,
        })
    return results

# ═══════════════════════════════════════════════
# 7. 市场状态检测与信号评分
# ═══════════════════════════════════════════════
def detect_market_regime(df, current_price):
    if "atr" not in df.columns or df["atr"].isna().all():
        return "UNKNOWN"
        
    atr = float(df["atr"].iloc[-1])
    atr_sma = float(df["atr"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else atr
    atr_ratio = atr / atr_sma if atr_sma > 0 else 1.0
    
    ma_f = float(df["ma_fast"].iloc[-1])
    ma_u = float(df["ma_ultra"].iloc[-1])
    ma_spread = abs(ma_f - ma_u) / current_price * 100
    
    adx = float(df["adx"].iloc[-1]) if "adx" in df.columns and not np.isnan(df["adx"].iloc[-1]) else 0
    bb_width = float(df["bb_width"].iloc[-1]) if "bb_width" in df.columns and not np.isnan(df["bb_width"].iloc[-1]) else 0
    
    trending_signals = 0
    if atr_ratio > 1.2: trending_signals += 1
    if ma_spread > 1.0: trending_signals += 1
    if adx > 25: trending_signals += 1
    if bb_width > 0.06: trending_signals += 1
    
    if trending_signals >= 3:
        return "TRENDING"
    elif trending_signals <= 1 and (adx < 20 or bb_width < 0.03):
        return "RANGING"
    else:
        return "TRANSITIONING"

def score_signal(direction, df, htf_trends, regime, cfg, btc_alignment=None, timeframe=None):
    if direction == "NONE":
        return 0, "AVOID", {}
        
    score = 0
    details = {}
    latest = df.iloc[-1]
    is_long = direction == "LONG"
    
    # 1. MA排列 (20分)
    ma_f = float(latest["ma_fast"])
    ma_s = float(latest["ma_slow"])
    ma_u_raw = latest["ma_ultra"]
    ma_u = float(ma_u_raw) if ma_u_raw is not None and not (isinstance(ma_u_raw, float) and np.isnan(ma_u_raw)) else None
    
    if ma_u is not None:
        if (is_long and ma_f > ma_s > ma_u) or (not is_long and ma_f < ma_s < ma_u):
            score += 20
            details["ma_alignment"] = "PERFECT"
        else:
            details["ma_alignment"] = "PARTIAL"
    else:
        if (is_long and ma_f > ma_s) or (not is_long and ma_f < ma_s):
            score += 15
            details["ma_alignment"] = "TWO_MA_ALIGNED"
        else:
            details["ma_alignment"] = "PARTIAL"
            
    # 2. MA斜率 (10分)
    slopes = [float(latest.get("ma_fast_slope", 0)), float(latest.get("ma_slow_slope", 0))]
    if (is_long and all(s > 0.05 for s in slopes)) or (not is_long and all(s < -0.05 for s in slopes)):
        score += 10
        details["ma_slope"] = "ALIGNED"
    elif all(abs(s) > 0.02 for s in slopes):
        score += 5
        details["ma_slope"] = "WEAK"
    else:
        details["ma_slope"] = "FLAT"
        
    # 3. ADX趋势强度 (10分) — 新增
    adx = float(latest.get("adx", 0))
    if not np.isnan(adx):
        if adx > cfg["adx_strong_trend"]:
            score += 10
            details["adx"] = f"STRONG({adx:.0f})"
        elif adx > 20:
            score += 5
            details["adx"] = f"MODERATE({adx:.0f})"
        else:
            details["adx"] = f"WEAK({adx:.0f})"
            
    # 4. RSI确认 (10分)
    rsi = float(latest.get("rsi", 50))
    if not np.isnan(rsi):
        if is_long and 30 <= rsi <= 55:
            score += 10
            details["rsi"] = f"IDEAL({rsi:.0f})"
        elif not is_long and 45 <= rsi <= 70:
            score += 10
            details["rsi"] = f"IDEAL({rsi:.0f})"
        elif 35 <= rsi <= 65:
            score += 5
            details["rsi"] = f"OK({rsi:.0f})"
        else:
            details["rsi"] = f"EXTREME({rsi:.0f})"
            
    # 5. MACD确认 (10分)
    macd_hist = float(latest.get("macd_hist", 0))
    if not np.isnan(macd_hist):
        prev_hist = float(df["macd_hist"].iloc[-2]) if len(df) >= 2 else 0
        if (is_long and macd_hist > 0 and macd_hist > prev_hist) or \
           (not is_long and macd_hist < 0 and macd_hist < prev_hist):
            score += 10
            details["macd"] = "CONFIRM_EXPAND"
        elif (is_long and macd_hist > 0) or (not is_long and macd_hist < 0):
            score += 5
            details["macd"] = "CONFIRM"
        else:
            details["macd"] = "DIVERGE"
            
    # 6. 成交量 (10分)
    vol_ratio = float(latest.get("vol_ratio", 1.0))
    if not np.isnan(vol_ratio):
        if vol_ratio >= 1.5:
            score += 10
            details["volume"] = f"HIGH({vol_ratio:.1f}x)"
        elif vol_ratio >= 1.0:
            score += 7
            details["volume"] = f"NORMAL({vol_ratio:.1f}x)"
        elif vol_ratio >= 0.7:
            score += 3
            details["volume"] = f"LOW({vol_ratio:.1f}x)"
        else:
            details["volume"] = f"VERY_LOW({vol_ratio:.1f}x)"
            
    # 7. 布林带位置 (5分) — 新增
    bb_pos = float(latest.get("bb_position", 0.5))
    if not np.isnan(bb_pos):
        if is_long and 0.2 <= bb_pos <= 0.5:
            score += 5
            details["bollinger"] = "LOWER_HALF"
        elif not is_long and 0.5 <= bb_pos <= 0.8:
            score += 5
            details["bollinger"] = "UPPER_HALF"
        elif (is_long and bb_pos > 0.9) or (not is_long and bb_pos < 0.1):
            details["bollinger"] = "EXTREME"
        else:
            score += 2
            details["bollinger"] = "NEUTRAL"
            
    # 8. K线形态 (5分) — 新增
    pattern = str(latest.get("candle_pattern", ""))
    if pattern:
        if (is_long and pattern in ("HAMMER", "BULL_ENGULF")) or \
           (not is_long and pattern in ("BEAR_ENGULF",)):
            score += 5
            details["pattern"] = pattern
        elif pattern == "DOJI":
            score += 2
            details["pattern"] = "DOJI"
        else:
            details["pattern"] = "NONE"
            
    # 9. 高级别趋势 (否决项 + 加分,不是单纯加分)
    if htf_trends:
        htf_main = htf_trends[0]
        htf_trend = htf_main.get("trend", "UNKNOWN")
        htf_conf = htf_main.get("confidence", 0)
        expected = "BULLISH" if is_long else "BEARISH"
        opposite = "BEARISH" if is_long else "BULLISH"
        veto_thresh = cfg.get("htf_veto_confidence", 50)
        
        if htf_trend == expected:
            score += 15
            details["htf"] = f"ALIGNED(conf={htf_conf})"
        elif htf_trend == "NEUTRAL":
            score += 5
            details["htf"] = "NEUTRAL"
        elif htf_trend == opposite:
            if htf_conf >= veto_thresh:
                # 高置信度逆向 → 一票否决
                details["htf"] = f"VETO(conf={htf_conf})"
                return 0, "AVOID", details
            else:
                score -= 25
                details["htf"] = f"AGAINST(conf={htf_conf})"
        else:
            details["htf"] = "UNKNOWN"
            
    # 10. 市场状态 (5分)
    if regime == "TRENDING":
        score += 5
        details["regime"] = "TRENDING"
    elif regime == "TRANSITIONING":
        score += 2
        details["regime"] = "TRANSITIONING"
    else:
        details["regime"] = regime
        
    # 11. 背离扣分 (-15)
    div_type, div_reason = detect_divergence(df, direction)
    adverse = (is_long and div_type == "BEARISH") or (not is_long and div_type == "BULLISH")
    if adverse:
        score -= 15
        details["divergence"] = f"ADVERSE: {div_reason}"
    else:
        details["divergence"] = "NONE"
        
    # 12. BTC 主导对齐 (alt 标的:同向+5,反向且高置信-15)
    if btc_alignment:
        btc_trend = btc_alignment.get("trend")
        btc_conf = btc_alignment.get("confidence", 0)
        expected_btc = "BULLISH" if is_long else "BEARISH"
        opposite_btc = "BEARISH" if is_long else "BULLISH"
        veto_thresh = cfg.get("htf_veto_confidence", 50)
        
        if btc_trend == expected_btc:
            score += 5
            details["btc_align"] = f"ALIGNED(conf={btc_conf})"
        elif btc_trend == opposite_btc and btc_conf >= veto_thresh:
            score -= 15
            details["btc_align"] = f"AGAINST(conf={btc_conf})"
        else:
            details["btc_align"] = f"{btc_trend}(conf={btc_conf})" if btc_trend else "UNKNOWN"
            
    # 13. VWAP 位置 (仅日内周期 5m/15m/1h, ±5)
    if timeframe in ("5m", "15m", "1h") and "vwap" in df.columns:
        vwap_val = float(df["vwap"].iloc[-1]) if not np.isnan(df["vwap"].iloc[-1]) else None
        close_val = float(df["close"].iloc[-1])
        if vwap_val:
            ratio = (close_val - vwap_val) / vwap_val
            if (is_long and ratio > 0.001) or (not is_long and ratio < -0.001):
                score += 5
                details["vwap"] = f"FAVORABLE({ratio*100:.2f}%)"
            elif abs(ratio) <= 0.001:
                score += 2
                details["vwap"] = "NEUTRAL"
            else:
                score -= 3
                details["vwap"] = f"UNFAVORABLE({ratio*100:.2f}%)"

    score = max(0, min(score, 100))
    
    # 评级
    if score >= 75:
        quality = "STRONG"
    elif score >= 55:
        quality = "MODERATE"
    elif score >= 35:
        quality = "WEAK"
    else:
        quality = "AVOID"
        
    return score, quality, details

# ═══════════════════════════════════════════════
# 8. 风控计算
# ═══════════════════════════════════════════════
def _find_recent_swing(df, direction, lookback=20, fractal_n=2):
    """
    fractal swing 检测:当前 K 线 high/low 比左右各 fractal_n 根都极值 → swing point。
    返回 lookback 内最新的 swing low (LONG) 或 swing high (SHORT) 价格,无则 None。
    """
    if df is None or len(df) < lookback + fractal_n * 2 + 1:
        return None
    if "high" not in df.columns or "low" not in df.columns:
        return None
        
    recent = df.tail(lookback + fractal_n * 2)
    highs = recent["high"].values
    lows = recent["low"].values
    n = len(recent)
    swings = []
    
    for i in range(fractal_n, n - fractal_n):
        if direction == "LONG":
            window = lows[i - fractal_n:i + fractal_n + 1]
            if lows[i] == window.min() and lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                swings.append((i, float(lows[i])))
        else:
            window = highs[i - fractal_n:i + fractal_n + 1]
            if highs[i] == window.max() and highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                swings.append((i, float(highs[i])))
                
    if not swings:
        return None
    return swings[-1][1]

def calc_stop_loss(entry_price, direction, atr, risk_pct, cfg, df=None):
    """
    混合止损:取 ATR 止损与 swing 结构止损中更保守(LONG 取更低,SHORT 取更高)。
    返回 (price, meta_dict)。meta 包含使用的止损类型与参考价。
    """
    mult = cfg["atr_stop_multiplier"]
    swing_buffer_atr = cfg.get("swing_buffer_atr", 0.3)
    swing_lookback = cfg.get("swing_lookback", 20)
    fractal_n = cfg.get("swing_fractal_n", 2)
    
    atr_valid = atr is not None and atr > 0 and not (isinstance(atr, float) and np.isnan(atr))
    swing_anchor = None
    
    if df is not None and atr_valid:
        swing_anchor = _find_recent_swing(df, direction, lookback=swing_lookback, fractal_n=fractal_n)
        
    if atr_valid:
        atr_stop = entry_price - atr * mult if direction == "LONG" else entry_price + atr * mult
        if swing_anchor is not None:
            structural = (swing_anchor - swing_buffer_atr * atr) if direction == "LONG" \
                else (swing_anchor + swing_buffer_atr * atr)
            # 取更保守:LONG 取更低,SHORT 取更高
            stop = min(atr_stop, structural) if direction == "LONG" else max(atr_stop, structural)
            return stop, {
                "type": "ATR_STRUCTURE_HYBRID",
                "atr_multiplier": mult,
                "swing_anchor": swing_anchor,
                "atr_stop": atr_stop,
                "structural_stop": structural,
            }
        return atr_stop, {
            "type": "ATR",
            "atr_multiplier": mult,
            "swing_anchor": None,
        }
        
    # 无 ATR 时回退固定百分比
    fallback = entry_price * (1 - risk_pct) if direction == "LONG" else entry_price * (1 + risk_pct)
    return fallback, {"type": "FIXED_PCT", "atr_multiplier": None, "swing_anchor": None}

def pick_entry_strategy(direction, df, current_price, ma20, ma60, atr, signal_quality, regime, cfg):
    """
    根据信号强度+市场状态自动选 primary 入场策略,并提供三档备选。
    返回 { "primary": {"type", "price", "label", "rationale"}, "alternatives": [{"zone", "type", "price", "label"}, ...] }
    """
    is_long = direction == "LONG"
    atr_val = atr if atr is not None and atr > 0 and not (isinstance(atr, float) and np.isnan(atr)) else None
    atr_offset = atr_val * 0.3 if atr_val else current_price * 0.003
    
    # 检测最近是否刚刚形成 MA 排列(MA20/MA60 在近 5 根内交叉)
    just_crossed = False
    if df is not None and len(df) >= 7 and "ma_fast" in df.columns and "ma_slow" in df.columns:
        recent = df.tail(6)
        diff = (recent["ma_fast"] - recent["ma_slow"]).values
        signs = np.sign(diff)
        just_crossed = bool(np.any(np.diff(signs) != 0))
        
    # 检测是否突破近 20 根高/低点
    broke_out = False
    if df is not None and len(df) >= 21 and "high" in df.columns and "low" in df.columns:
        prior = df.iloc[-21:-1]  # 排除当前 K 线
        if is_long:
            broke_out = current_price > float(prior["high"].max())
        else:
            broke_out = current_price < float(prior["low"].min())
            
    # 自动选择 primary
    if signal_quality == "STRONG" and regime == "TRENDING" and broke_out:
        primary_type = "BREAKOUT"
        primary_price = current_price + atr_offset if is_long else current_price - atr_offset
        rationale = "强趋势 + 突破近 20 根高/低,追单入场"
        primary_label = "突破追单" if is_long else "破位追空"
    elif just_crossed and ((is_long and current_price > ma20) or (not is_long and current_price < ma20)):
        primary_type = "RECLAIM"
        primary_price = ma20 + atr_offset * 0.67 if is_long else ma20 - atr_offset * 0.67
        rationale = "近 5 根 MA20/60 刚交叉 + 价格站稳 MA20,失而复得入场"
        primary_label = "失而复得 (Reclaim)"
    else:
        primary_type = "PULLBACK"
        primary_price = ma20
        rationale = "回踩 MA20 入场,等待价格回到关键均线"
        primary_label = "MA20 回踩" if is_long else "MA20 反抽"
        
    # 三档备选(aggressive / moderate / conservative)
    aggressive_price = current_price + atr_offset if is_long else current_price - atr_offset
    moderate_price = ma20
    conservative_price = ma60
    
    alternatives = [
        {"zone": "aggressive", "type": "BREAKOUT", "price": aggressive_price, "label": "现价追单 (±0.3 ATR)", "rationale": "不等回踩,追强趋势"},
        {"zone": "moderate", "type": "PULLBACK", "price": moderate_price, "label": "MA20 回踩" if is_long else "MA20 反抽", "rationale": "等待回到 MA20"},
        {"zone": "conservative", "type": "DEEP_PULLBACK", "price": conservative_price, "label": "MA60 深度回踩" if is_long else "MA60 深度反抽", "rationale": "等待深度回踩,胜率最高"},
    ]
    
    return {
        "primary": {
            "type": primary_type,
            "price": primary_price,
            "label": primary_label,
            "rationale": rationale,
            "broke_out": broke_out,
            "just_crossed": just_crossed,
        },
        "alternatives": alternatives,
    }

def calc_take_profit_levels(entry_price, stop_loss, direction, signal_score, regime, cfg):
    risk = abs(entry_price - stop_loss)
    ratios = cfg["partial_tp_ratios"]
    rr_targets = list(cfg["partial_tp_rr"])
    
    if regime == "TRENDING" and signal_score >= 75:
        rr_targets = [r * 1.5 for r in rr_targets]
    elif signal_score < 50:
        rr_targets = [max(1.0, r * 0.75) for r in rr_targets]
        
    levels = []
    for i, (ratio, rr) in enumerate(zip(ratios, rr_targets)):
        if direction == "LONG":
            tp_price = entry_price + risk * rr
        else:
            tp_price = entry_price - risk * rr
        levels.append({
            "level": i + 1,
            "price": tp_price,
            "close_ratio": ratio,
            "rr": rr,
        })
    return levels

def calc_trailing_stop(entry_price, stop_loss, current_price, direction, cfg, df=None):
    """
    Chandelier Exit 追踪止损:
    - 激活门槛:浮盈达 trailing_stop_activation_rr × R
    - 激活后:LONG = highest_high(N) - mult × ATR(N), SHORT = lowest_low(N) + mult × ATR(N)
    - 不低于成本(LONG)/不高于成本(SHORT)
    df 不可用时回退到原 R 倍率回调逻辑。
    """
    risk = abs(entry_price - stop_loss)
    activation_rr = cfg["trailing_stop_activation_rr"]
    atr_mult = cfg.get("trailing_atr_mult", 3.0)
    lookback = cfg.get("trailing_lookback", 22)
    callback = cfg.get("trailing_stop_callback", 0.5)
    
    if direction == "LONG":
        pnl = current_price - entry_price
        if pnl < risk * activation_rr:
            return None
        if df is not None and "atr" in df.columns and len(df) >= lookback:
            recent_atr = float(df["atr"].iloc[-1])
            if not np.isnan(recent_atr) and recent_atr > 0:
                recent_high = float(df["high"].tail(lookback).max())
                chandelier = recent_high - atr_mult * recent_atr
                return max(chandelier, entry_price)
        trail_stop = current_price - risk * callback
        return max(trail_stop, entry_price)
    else:
        pnl = entry_price - current_price
        if pnl < risk * activation_rr:
            return None
        if df is not None and "atr" in df.columns and len(df) >= lookback:
            recent_atr = float(df["atr"].iloc[-1])
            if not np.isnan(recent_atr) and recent_atr > 0:
                recent_low = float(df["low"].tail(lookback).min())
                chandelier = recent_low + atr_mult * recent_atr
                return min(chandelier, entry_price)
        trail_stop = current_price + risk * callback
        return min(trail_stop, entry_price)

def calc_breakeven_stop(entry_price, stop_loss, current_price, direction, fee_rate, cfg):
    risk = abs(entry_price - stop_loss)
    activation_rr = cfg["breakeven_activation_rr"]
    if direction == "LONG":
        pnl = current_price - entry_price
        if pnl >= risk * activation_rr:
            return entry_price + entry_price * fee_rate * 2
    else:
        pnl = entry_price - current_price
        if pnl >= risk * activation_rr:
            return entry_price - entry_price * fee_rate * 2
    return None

def calc_liquidation_price(entry_price, leverage, direction, maint_margin_rate=0.005):
    if leverage <= 1:
        return None
    if direction == "LONG":
        return entry_price * (1 - (1 / leverage) + maint_margin_rate)
    else:
        return entry_price * (1 + (1 / leverage) - maint_margin_rate)

def _estimate_holding_days(timeframe, target_rr, cfg):
    """
    根据 timeframe 与目标 RR 粗估持仓天数。
    经验估算:达到 N×R 大约需要 5×N 根 K 线(强趋势更快,弱趋势更慢)。
    """
    bars_per_r = 5
    estimated_bars = bars_per_r * max(target_rr, 1)
    minutes = TIMEFRAME_MINUTES.get(timeframe, 60) * estimated_bars
    return round(minutes / (60 * 24), 2)

def calc_position_size(entry_price, stop_loss, direction, balance, leverage, exchange_id, risk_pct, cfg, is_maker=False, timeframe=None, target_rr=2.0):
    if balance is None or balance <= 0:
        return None
        
    is_spot = leverage <= 1
    if is_spot:
        fee_rate = FEE_RATES.get(exchange_id, FEE_RATES["binance"])["spot"]
    else:
        fee_key = "futures_maker" if is_maker else "futures_taker"
        fee_rate = FEE_RATES.get(exchange_id, FEE_RATES["binance"])[fee_key]
        
    if direction == "LONG":
        stop_distance = (entry_price - stop_loss) / entry_price
    else:
        stop_distance = (stop_loss - entry_price) / entry_price
        
    if stop_distance <= 0:
        return None
        
    max_loss = balance * risk_pct
    max_account_loss = balance * cfg["max_account_risk"]
    max_loss = min(max_loss, max_account_loss)
    fee_cost = fee_rate * 2
    
    # funding 占用 risk_per_trade 预算的一部分(仅合约;现货不计)
    is_spot_no_funding = leverage <= 1 or not timeframe
    funding_pct = 0.0
    holding_days = 0.0
    daily_funding = cfg.get("funding_rate_daily_estimate", 0.0003)
    
    if not is_spot_no_funding:
        holding_days = _estimate_holding_days(timeframe, target_rr, cfg)
        funding_pct = daily_funding * holding_days
        
    real_stop_cost = stop_distance + fee_cost + funding_pct
    nominal_position = max_loss / real_stop_cost
    margin = nominal_position / max(leverage, 1)
    quantity = nominal_position / entry_price
    liq_price = calc_liquidation_price(entry_price, leverage, direction)
    
    pos = {
        "nominal_position_usdt": round(nominal_position, 2),
        "margin_usdt": round(margin, 2),
        "quantity": round(quantity, 6),
        "max_loss_usdt": round(max_loss, 2),
        "margin_ratio": round(margin / balance * 100, 2),
        "fee_rate": fee_rate,
        "liquidation_price": round(liq_price, 2) if liq_price else None,
        "distance_to_liq_pct": round(abs(entry_price - liq_price) / entry_price * 100, 2) if liq_price else None,
    }
    
    if not is_spot_no_funding:
        funding_cost = nominal_position * daily_funding * holding_days
        pos["holding_days_estimate"] = holding_days
        pos["funding_rate_daily_assumed"] = daily_funding
        pos["funding_cost_estimate_usdt"] = round(funding_cost, 2)
        
    return pos

# ═══════════════════════════════════════════════
# 9. 信号过滤器
# ═══════════════════════════════════════════════
def run_filters(direction, df, regime, cfg, btc_alignment=None):
    if direction == "NONE":
        return []
        
    latest = df.iloc[-1]
    is_long = direction == "LONG"
    filters = []
    
    rsi = float(latest.get("rsi", 50))
    if not np.isnan(rsi):
        if is_long and rsi > cfg["rsi_overbought"]:
            filters.append({"level": "WARNING", "msg": f"RSI 超买 ({rsi:.0f})", "action": "减仓或等待回调"})
        elif not is_long and rsi < cfg["rsi_oversold"]:
            filters.append({"level": "WARNING", "msg": f"RSI 超卖 ({rsi:.0f})", "action": "减仓或等待反弹"})
            
    macd_hist = float(latest.get("macd_hist", 0))
    if not np.isnan(macd_hist):
        if (is_long and macd_hist < 0) or (not is_long and macd_hist > 0):
            filters.append({"level": "CAUTION", "msg": "MACD 动能未确认", "action": "等待动能翻转"})
            
    vol_ratio = float(latest.get("vol_ratio", 1.0))
    if not np.isnan(vol_ratio) and vol_ratio < 0.5:
        filters.append({"level": "CAUTION", "msg": f"成交量过低 ({vol_ratio:.2f}x)", "action": "信号可靠性降低"})
        
    adx = float(latest.get("adx", 0))
    if not np.isnan(adx) and adx < 15:
        filters.append({"level": "WARNING", "msg": f"ADX 极低 ({adx:.0f})", "action": "市场无方向，不宜趋势交易"})
        
    bb_pos = float(latest.get("bb_position", 0.5))
    if not np.isnan(bb_pos):
        if is_long and bb_pos > 0.95:
            filters.append({"level": "CAUTION", "msg": "价格触及布林带上轨", "action": "可能短期回调"})
        elif not is_long and bb_pos < 0.05:
            filters.append({"level": "CAUTION", "msg": "价格触及布林带下轨", "action": "可能短期反弹"})
            
    if regime == "RANGING":
        filters.append({"level": "WARNING", "msg": "震荡市", "action": "均线系统容易反复止损，建议观望或降低仓位"})
        
    pattern = str(latest.get("candle_pattern", ""))
    if pattern:
        if (is_long and pattern in ("BEAR_ENGULF",)) or (not is_long and pattern in ("HAMMER", "BULL_ENGULF")):
            filters.append({"level": "CAUTION", "msg": f"反向K线形态: {pattern}", "action": "等待下一根K线确认"})
            
    # BTC 主导反向预警(alt 标的)
    if btc_alignment:
        btc_trend = btc_alignment.get("trend")
        btc_conf = btc_alignment.get("confidence", 0)
        expected_btc = "BULLISH" if direction == "LONG" else "BEARISH"
        opposite_btc = "BEARISH" if direction == "LONG" else "BULLISH"
        veto_thresh = cfg.get("htf_veto_confidence", 50)
        
        if btc_trend == opposite_btc and btc_conf >= veto_thresh:
            filters.append({
                "level": "WARNING", 
                "msg": f"BTC 同周期反向 ({btc_trend}, conf={btc_conf})", 
                "action": "alt 跟跌/跟涨概率高,建议降低仓位或观望",
            })
        elif btc_trend == opposite_btc:
            filters.append({
                "level": "CAUTION", 
                "msg": f"BTC 同周期偏弱反向 ({btc_trend}, conf={btc_conf})", 
                "action": "BTC 走势不利,小仓试探",
            })
            
    # 信号冷却：检测最近 N 根 K 线内是否发生过 ma20/ma60 交叉，避免刚换向就重仓追单
    cooldown_bars = cfg.get("signal_cooldown_bars", 0)
    if cooldown_bars > 0 and len(df) >= cooldown_bars + 2 and "ma_fast" in df.columns and "ma_slow" in df.columns:
        recent = df.tail(cooldown_bars + 1)
        diff = (recent["ma_fast"] - recent["ma_slow"]).values
        # 是否有正负号变化
        signs = np.sign(diff)
        crossed = bool(np.any(np.diff(signs) != 0))
        if crossed:
            filters.append({
                "level": "CAUTION", 
                "msg": f"近 {cooldown_bars} 根 K 线内 MA20/MA60 发生交叉", 
                "action": "信号尚未稳定，建议小仓试探或等趋势确认",
            })
            
    return filters

# ═══════════════════════════════════════════════
# 10. 核心分析引擎
# ═══════════════════════════════════════════════
def analyze(symbol, timeframe, df, current_price, ma20, ma60, ma120, cfg, balance=None, leverage=1, exchange_id="binance", htf_trends=None, btc_alignment=None):
    rnd = lambda v: _smart_round(v, current_price)
    risk_pct = cfg["risk_per_trade"]
    has_indicators = df is not None and "atr" in df.columns and not df["atr"].isna().all()
    
    result = {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": rnd(current_price),
        "ma": {"fast": rnd(ma20), "slow": rnd(ma60), "ultra": rnd(ma120)},
        "trend": "NEUTRAL",
        "signal_direction": "NONE",
    }
    
    # 读取指标
    latest = df.iloc[-1] if df is not None and len(df) > 0 else None
    atr_val = float(latest["atr"]) if latest is not None and has_indicators and not np.isnan(latest.get("atr", np.nan)) else None
    rsi_val = float(latest["rsi"]) if latest is not None and has_indicators and "rsi" in df.columns and not np.isnan(latest.get("rsi", np.nan)) else None
    adx_val = float(latest["adx"]) if latest is not None and has_indicators and "adx" in df.columns and not np.isnan(latest.get("adx", np.nan)) else None
    macd_hist_val = float(latest["macd_hist"]) if latest is not None and has_indicators and "macd_hist" in df.columns and not np.isnan(latest.get("macd_hist", np.nan)) else None
    vol_ratio_val = float(latest["vol_ratio"]) if latest is not None and has_indicators and "vol_ratio" in df.columns and not np.isnan(latest.get("vol_ratio", np.nan)) else None
    bb_pos = float(latest["bb_position"]) if latest is not None and has_indicators and "bb_position" in df.columns and not np.isnan(latest.get("bb_position", np.nan)) else None
    vwap_val = float(latest["vwap"]) if latest is not None and has_indicators and "vwap" in df.columns and not np.isnan(latest.get("vwap", np.nan)) else None
    support = float(latest["nearest_support"]) if latest is not None and "nearest_support" in df.columns and not np.isnan(latest.get("nearest_support", np.nan)) else None
    resistance = float(latest["nearest_resistance"]) if latest is not None and "nearest_resistance" in df.columns and not np.isnan(latest.get("nearest_resistance", np.nan)) else None
    pattern = str(latest.get("candle_pattern", "")) if latest is not None else ""
    
    regime = detect_market_regime(df, current_price) if has_indicators else "UNKNOWN"
    
    # 趋势判断（MA120 缺失时降级为双均线）
    if ma120 is not None:
        if ma20 > ma60 > ma120:
            direction, result["trend"] = "LONG", "BULLISH"
        elif ma20 < ma60 < ma120:
            direction, result["trend"] = "SHORT", "BEARISH"
        else:
            direction, result["trend"] = "NONE", "NEUTRAL"
    else:
        if ma20 > ma60:
            direction, result["trend"] = "LONG", "BULLISH"
        elif ma20 < ma60:
            direction, result["trend"] = "SHORT", "BEARISH"
        else:
            direction, result["trend"] = "NONE", "NEUTRAL"
            
    result["signal_direction"] = direction
    
    # 信号评分
    if has_indicators:
        signal_score, signal_quality, score_details = score_signal(
            direction, df, htf_trends, regime, cfg, btc_alignment=btc_alignment, timeframe=timeframe,
        )
    else:
        signal_score, signal_quality, score_details = (25 if direction != "NONE" else 0), ("MANUAL" if direction != "NONE" else "AVOID"), {}
        
    # 指标快照
    result["indicators"] = {
        "atr": rnd(atr_val),
        "rsi": round(rsi_val, 1) if rsi_val else None,
        "adx": round(adx_val, 1) if adx_val else None,
        "macd_histogram": rnd(macd_hist_val),
        "volume_ratio": round(vol_ratio_val, 2) if vol_ratio_val else None,
        "bb_position": round(bb_pos, 2) if bb_pos else None,
        "vwap": rnd(vwap_val),
        "candle_pattern": pattern if pattern else None,
        "nearest_support": rnd(support),
        "nearest_resistance": rnd(resistance),
    }
    
    result["market_regime"] = regime
    result["signal_score"] = signal_score
    result["signal_quality"] = signal_quality
    result["score_breakdown"] = score_details
    if htf_trends:
        result["htf_analysis"] = htf_trends
    if btc_alignment is not None:
        result["btc_alignment"] = btc_alignment
        
    # 过滤器
    filters = run_filters(direction, df, regime, cfg, btc_alignment=btc_alignment) if has_indicators else []
    result["filters"] = filters
    
    # 手动参考模式 — 缺少 ATR/RSI/ADX/成交量验证,不输出完整 trade_plan,避免用户盲狙
    if direction != "NONE" and not has_indicators:
        fallback_pct = 0.02
        fallback_sl = ma20 * (1 - fallback_pct) if direction == "LONG" else ma20 * (1 + fallback_pct)
        # 无 ATR 时改用 MA60 ± 1% 作 zone 下界(LONG 跌破 / SHORT 突破即趋势恶化)
        # zone_status 与 has_indicators 路径保持对称:in_zone / await_pullback / trend_breakdown
        zone_buffer_pct = 0.01
        if direction == "LONG":
            lower_bound = ma60 * (1 - zone_buffer_pct)
            if current_price > ma20 * (1 + zone_buffer_pct):
                zone_status = "await_pullback"
            elif current_price < lower_bound:
                zone_status = "trend_breakdown"
            else:
                zone_status = "in_zone"
        else:
            upper_bound = ma60 * (1 + zone_buffer_pct)
            if current_price < ma20 * (1 - zone_buffer_pct):
                zone_status = "await_pullback"
            elif current_price > upper_bound:
                zone_status = "trend_breakdown"
            else:
                zone_status = "in_zone"
                
        in_zone = zone_status == "in_zone"
        result["trade_plan"] = {
            "mode": "manual_reference",
            "entry": {
                "price": rnd(ma20),
                "type": "LIMIT",
                "label": "MA20 回踩(概念性)",
                "in_strike_zone": in_zone,
                "zone_status": zone_status,
            },
            "stop_loss": {
                "initial": rnd(fallback_sl),
                "type": "FIXED_PCT_FALLBACK",
                "note": "缺少 ATR,使用 2% 固定止损;实际交易请用 ATR x 1.5 或 swing 结构止损",
            },
            "take_profit": [],
            "note": "手动模式仅有 MA 数据,缺少 ATR/RSI/ADX/成交量/K线形态/支撑阻力等验证,trade_plan 仅供方向参考",
        }
        result["action"] = {
            "code": "REFERENCE_ONLY",
            "message": "仅基于 MA 排列,缺少多维度验证;请补充实时数据(自动模式)后再做最终决策",
        }
        result["disclaimer"] = "仅供分析参考,不构成投资建议。手动模式无法验证趋势真伪,请勿据此重仓。"
        return result
        
    # 交易计划
    if direction != "NONE":
        # 多入场策略:自动选 primary,三档备选(aggressive/moderate/conservative)
        entry_plan = pick_entry_strategy(
            direction, df, current_price, ma20, ma60, atr_val, signal_quality, regime, cfg
        )
        entry_price = entry_plan["primary"]["price"]
        entry_strategy = entry_plan["primary"]["type"]
        stop_loss, sl_meta = calc_stop_loss(entry_price, direction, atr_val, risk_pct, cfg, df=df)
        tp_levels = calc_take_profit_levels(entry_price, stop_loss, direction, signal_score, regime, cfg)
        
        # in_zone 含下界:LONG 时价格必须在 [MA60 - 0.5 ATR, entry_price] 区间
        # zone_status 语义统一(对 LONG/SHORT 对称):
        # in_zone = 价格仍在击球区,可正常入场
        # await_pullback = 趋势同向但已离开击球区,需等回踩
        # trend_breakdown = 价格已突破 MA60±buffer,趋势恶化,降级 CAUTION
        atr_buffer = (atr_val * 0.5) if atr_val and not np.isnan(atr_val) else current_price * 0.005
        
        if direction == "LONG":
            lower_bound = ma60 - atr_buffer
            if current_price > entry_price:
                zone_status = "await_pullback"
            elif current_price < lower_bound:
                zone_status = "trend_breakdown"
            else:
                zone_status = "in_zone"
        else:
            upper_bound = ma60 + atr_buffer
            if current_price < entry_price:
                zone_status = "await_pullback"
            elif current_price > upper_bound:
                zone_status = "trend_breakdown"
            else:
                zone_status = "in_zone"
                
        is_in_zone = zone_status == "in_zone"
        fee_rate = FEE_RATES.get(exchange_id, FEE_RATES["binance"]).get("futures_taker", 0.0005)
        
        trade_plan = {
            "entry": {
                "price": rnd(entry_price),
                "type": "LIMIT",
                "strategy": entry_strategy,
                "label": entry_plan["primary"]["label"],
                "rationale": entry_plan["primary"]["rationale"],
                "in_strike_zone": is_in_zone,
                "zone_status": zone_status,
                "broke_out": entry_plan["primary"].get("broke_out", False),
                "just_crossed": entry_plan["primary"].get("just_crossed", False),
            },
            "stop_loss": {
                "initial": rnd(stop_loss),
                "type": sl_meta.get("type", "ATR"),
                "atr_multiplier": sl_meta.get("atr_multiplier"),
                "swing_anchor": rnd(sl_meta.get("swing_anchor")) if sl_meta.get("swing_anchor") else None,
                "atr_stop": rnd(sl_meta.get("atr_stop")) if sl_meta.get("atr_stop") else None,
                "structural_stop": rnd(sl_meta.get("structural_stop")) if sl_meta.get("structural_stop") else None,
            },
            "take_profit": [
                {"level": tp["level"], "price": rnd(tp["price"]), "close_pct": round(tp["close_ratio"] * 100), "rr": round(tp["rr"], 1)}
                for tp in tp_levels
            ],
            "risk_management": {
                "trailing_stop": {
                    "activation": f"{cfg['trailing_stop_activation_rr']}R",
                    "style": "CHANDELIER",
                    "atr_multiplier": cfg.get("trailing_atr_mult", 3.0),
                    "lookback": cfg.get("trailing_lookback", 22),
                    "current_trail_price": rnd(calc_trailing_stop(entry_price, stop_loss, current_price, direction, cfg, df=df)),
                },
                "breakeven_at": f"{cfg['breakeven_activation_rr']}R",
            },
        }
        
        # 三档入场备选(aggressive/moderate/conservative) + 支撑/阻力位
        entry_zones = [
            {
                "zone": alt["zone"],
                "type": alt["type"],
                "price": rnd(alt["price"]),
                "label": alt["label"],
                "rationale": alt["rationale"],
            }
            for alt in entry_plan["alternatives"]
        ]
        if support and direction == "LONG":
            entry_zones.append({"zone": "support", "type": "STRUCTURAL", "price": rnd(support), "label": "支撑位"})
        if resistance and direction == "SHORT":
            entry_zones.append({"zone": "resistance", "type": "STRUCTURAL", "price": rnd(resistance), "label": "阻力位"})
            
        trade_plan["entry_zones"] = entry_zones
        result["trade_plan"] = trade_plan
        
        # 仓位计算
        if balance:
            target_rr = tp_levels[-1]["rr"] if tp_levels else 2.0
            pos = calc_position_size(
                entry_price, stop_loss, direction, balance, leverage, exchange_id, risk_pct, cfg, timeframe=timeframe, target_rr=target_rr,
            )
            if pos:
                qty = pos["quantity"]
                notional = pos["nominal_position_usdt"]
                if tp_levels:
                    weighted_gross = 0.0
                    for tp in tp_levels:
                        leg_pnl = (tp["price"] - entry_price) * qty if direction == "LONG" \
                            else (entry_price - tp["price"]) * qty
                        weighted_gross += leg_pnl * tp["close_ratio"]
                    expected_profit = weighted_gross - notional * fee_rate * 2
                    weighted_rr = sum(tp["rr"] * tp["close_ratio"] for tp in tp_levels)
                else:
                    fallback_tp = entry_price + abs(entry_price - stop_loss) * 2 if direction == "LONG" \
                        else entry_price - abs(entry_price - stop_loss) * 2
                    leg_pnl = (fallback_tp - entry_price) * qty if direction == "LONG" \
                        else (entry_price - fallback_tp) * qty
                    expected_profit = leg_pnl - notional * fee_rate * 2
                    weighted_rr = 2.0
                    
                funding_cost = pos.get("funding_cost_estimate_usdt", 0)
                pos["expected_profit_usdt"] = round(expected_profit, 2)
                pos["expected_profit_after_funding_usdt"] = round(expected_profit - funding_cost, 2)
                pos["weighted_rr_target"] = round(weighted_rr, 2)
                pos["risk_reward_ratio"] = round(expected_profit / pos["max_loss_usdt"], 2) if pos["max_loss_usdt"] > 0 else 0
                pos["risk_reward_ratio_after_funding"] = round((expected_profit - funding_cost) / pos["max_loss_usdt"], 2) if pos["max_loss_usdt"] > 0 else 0
                result["position"] = pos
                
        # 操作建议
        warning_count = sum(1 for f in filters if f["level"] == "WARNING")
        
        # 价格已跌破/突破 MA60-buffer:趋势健康度恶化,强制降级
        zone_breakdown = zone_status == "trend_breakdown"
        
        if signal_quality == "AVOID":
            # HTF 一票否决等场景:即便 in_zone 也不开仓
            action = "NO_TRADE"
            htf_note = score_details.get("htf", "")
            if htf_note.startswith("VETO"):
                action_cn = "高级别趋势反向(高置信度),放弃本笔"
            else:
                action_cn = "信号评级 AVOID,不交易"
        elif zone_breakdown:
            action = "CAUTION"
            action_cn = "价格已脱离 MA20-MA60 区间,趋势健康度恶化,等待重新站稳"
        elif is_in_zone and signal_quality in ("STRONG", "MODERATE") and warning_count == 0:
            action = "EXECUTE"
            action_cn = "可以入场"
        elif is_in_zone and signal_quality == "WEAK":
            action = "WAIT_CONFIRM"
            action_cn = "等待下一根K线确认"
        elif not is_in_zone:
            action = "WAIT_PULLBACK"
            action_cn = f"等待回踩 {rnd(entry_price)}" if direction == "LONG" else f"等待反弹 {rnd(entry_price)}"
        else:
            action = "CAUTION"
            action_cn = "信号存在风险，谨慎操作"
            
        result["action"] = {"code": action, "message": action_cn}
    else:
        result["trade_plan"] = None
        result["action"] = {"code": "NO_TRADE", "message": "均线缠绕，建议观望"}
        
    result["disclaimer"] = "仅供分析参考，不构成投资建议。严格执行止损纪律。"
    return result

# ═══════════════════════════════════════════════
# 11. CLI
# ═══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description=f"双均线/三均线交易系统 v{VERSION}",
        epilog="快捷用法: python3 auto_dual_ma_skill.py 82000 81500 81000 80000",
    )
    parser.add_argument("values", nargs="*", type=float, help="快捷输入: 价格 MA20 MA60 [MA120]")
    parser.add_argument("-s", "--symbol", default="BTC/USDT", help="交易品种")
    parser.add_argument("-t", "--timeframe", default="1d", help="时间周期")
    parser.add_argument("-r", "--risk", type=float, default=0.02, help="单笔风险比例")
    parser.add_argument("--price", type=float, help="当前价格")
    parser.add_argument("--ma20", type=float)
    parser.add_argument("--ma60", type=float)
    parser.add_argument("--ma120", type=float)
    parser.add_argument("--mtf", action="store_true", help="多时间框架分析")
    parser.add_argument("--mtf-depth", type=int, default=1, choices=[1, 2])
    parser.add_argument("--no-btc-check", action="store_true", help="关闭 BTC 主导对齐检查 (alt 标的默认开启)")
    parser.add_argument("-b", "--balance", type=float, help="账户余额(USDT)")
    parser.add_argument("-l", "--leverage", type=int, default=1, help="杠杆倍数")
    parser.add_argument("-e", "--exchange", default="binance", choices=list(FEE_RATES.keys()))
    parser.add_argument("--config", type=str, help="配置文件路径 (JSON/YAML)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="仅输出JSON，不打印额外信息")
    
    args = parser.parse_args()
    
    if args.values:
        if len(args.values) < 3:
            parser.error("快捷模式至少需要3个数字: 价格 MA20 MA60 [MA120]")
        args.price = args.values[0]
        args.ma20 = args.values[1]
        args.ma60 = args.values[2]
        args.ma120 = args.values[3] if len(args.values) >= 4 else None
        
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)
        
    cfg = load_config(args.config)
    
    if not (0 < args.risk <= 0.1):
        print(f"警告: 风险比例 {args.risk} 超出合理范围，已重置为 0.02", file=sys.stderr)
        args.risk = 0.02
        
    cfg["risk_per_trade"] = args.risk
    has_manual = args.price is not None and args.ma20 is not None and args.ma60 is not None
    
    if has_manual:
        if not args.json:
            print(f"手动输入 | 价格: ,.2f | MA20: ,.2f | MA60: ,.2f", end="")
            
        # 缺 MA120 时用 None，analyze() 内部会跳过 ultra 检查，避免 ma60==ma120 导致永远 NEUTRAL
        ma120 = args.ma120 if args.ma120 else None
        if args.ma120 and not args.json:
            print(f" | MA120: ,.2f")
        elif not args.json:
            print(" | MA120: 未提供(将跳过趋势线检查)")
            
        df = pd.DataFrame([{
            "close": args.price, "open": args.price, "high": args.price, "low": args.price,
            "volume": 0, "timestamp": pd.Timestamp.now(tz="UTC"), "ma_fast": args.ma20,
            "ma_slow": args.ma60, "ma_ultra": ma120, "atr": np.nan, "rsi": np.nan,
            "macd_hist": np.nan, "macd": np.nan, "macd_signal": np.nan, "vol_ratio": np.nan,
            "vol_sma": np.nan, "ma_fast_slope": np.nan, "ma_slow_slope": np.nan,
            "ma_ultra_slope": np.nan, "adx": np.nan, "plus_di": np.nan, "minus_di": np.nan,
            "bb_upper": np.nan, "bb_middle": np.nan, "bb_lower": np.nan, "bb_width": np.nan,
            "bb_position": np.nan, "vwap": np.nan, "candle_pattern": "", "tr": np.nan,
            "nearest_support": np.nan, "nearest_resistance": np.nan,
        }])
        current_price, htf_trends = args.price, None
    else:
        if not args.json:
            print(f"获取 {args.symbol} {args.timeframe} 数据...")
            
        df, data = fetch_market_data(args.symbol, args.timeframe, cfg)
        if df is None:
            print("数据获取失败。手动用法: python3 auto_dual_ma_skill.py 82000 81500 81000 80000", file=sys.stderr)
            sys.exit(1)
            
        current_price = data["current_price"]
        args.ma20, args.ma60, ma120 = data["ma20"], data["ma60"], data["ma120"]
        args.exchange = data.get("source", args.exchange)
        
        if not args.json:
            print(f"数据源: {data.get('source', '?')} | 价格: ,.2f | MA20: ,.2f | MA60: ,.2f | MA120: ,.2f")
            
        htf_trends = None
        if args.mtf:
            if not args.json:
                print("获取高级别趋势...")
            htf_trends = fetch_htf_trend(args.symbol, args.timeframe, cfg, args.mtf_depth)
            
    btc_alignment = None
    if not args.no_btc_check:
        if not args.json:
            print("检查 BTC 主导对齐...")
        btc_alignment = fetch_btc_alignment(args.symbol, args.timeframe, cfg)
        
    if has_manual:
        btc_alignment = None
        
    result = analyze(
        symbol=args.symbol,
        timeframe=args.timeframe,
        df=df,
        current_price=current_price,
        ma20=args.ma20,
        ma60=args.ma60,
        ma120=ma120,
        cfg=cfg,
        balance=args.balance,
        leverage=args.leverage,
        exchange_id=args.exchange,
        htf_trends=htf_trends,
        btc_alignment=btc_alignment,
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

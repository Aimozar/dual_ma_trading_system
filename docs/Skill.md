# 双均线交易系统 Skill 文档

## 简介

该系统基于 EMA20 / MA60 / MA120 进行趋势判断，并结合 ADX、VWAP、布林带、ATR 风控、HTF 否决、资金费率等模块进行综合分析。

## 核心功能

- 双均线 / 三均线趋势分析
- BREAKOUT / PULLBACK / RECLAIM 多入场模式
- ATR + Swing Stop Loss
- Chandelier Trailing Stop
- BTC 消息面分析
- 诱多反杀识别
- 风险收益比控制

## 风控规则

- 最低 RR >= 3
- 单笔风险 <= 2%
- 禁止浮亏加仓
- 禁止锁仓
- HTF 反向否决

## 使用方式

结合 K 线、成交量、宏观消息面与 BTC 主导趋势共同判断。

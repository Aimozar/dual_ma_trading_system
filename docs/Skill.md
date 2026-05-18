-----

## name: dual-ma-trading
description: >
Use this skill for crypto trend analysis and trade decisions involving
EMA20/MA60/MA120 alignment, entry/stop/take-profit planning, position sizing,
risk-reward checks, and 诱多反杀 (false-breakout reversal) short setups on
BTC/ETH/SOL. Also use when the user asks about BTC 消息面 / 利好利空 /
ETF 资金流 / 美联储 / CPI / FOMC / 监管 / 清算 / 资金费率, or whether
current news supports a long/short BTC trade.

# 双均线 / 三均线交易系统 v3.2 — 生产级

> 项目文件采用根目录扁平结构。不要创建 `scripts/` 文件夹。将 `Skill.md`、
> `auto_dual_ma_skill.py`、`btc_news_skill.py` 直接放在项目根目录。

基于 EMA20 / MA60 / MA120 排列的加密货币趋势跟踪系统。
集成 ADX / 布林带 / VWAP / K 线形态 / BTC 主导对齐、多入场策略
（BREAKOUT / PULLBACK / RECLAIM）、ATR + swing 结构混合止损、
Chandelier 追踪止损、资金费率成本估算、HTF 反向一票否决、
**空头偏向模式**、**诱多反杀形态识别**、**硬性风控规则**。

-----

## ⚠️ 硬性风控规则（Hard Rules，不可绕过）

这些规则在任何模式下都强制执行,模型不得为”看起来很好的机会”做例外。

1. **最低盈亏比 3:1**。`avg_rr < 3.0` 的方案一律标记 `REFERENCE_ONLY`,不出 `EXECUTE`。
1. **杠杆 + 浮亏 + 移动止损 = 大忌**。浮亏状态下:
- 禁止加仓(任何形式的”补仓 / 摊低成本 / 加保证金”)。
- 禁止把止损往不利方向移动,只能往保本方向移。
- 禁止解除已设置的止损单。
1. **单笔风险硬上限 2%**。`risk_per_trade > 0.02` 时直接拒绝,即使用户手动指定。
1. **不锁仓**。已有空头时不开多头对冲,反之亦然。要么平仓要么持有。
1. **未完成复盘禁止开新仓**。如果上一笔亏损交易未生成 post-mortem,新信号一律 `REFERENCE_ONLY`。
1. **强趋势加仓也必须有独立止损**,不与原仓共用。
1. **手动模式永远输出 `REFERENCE_ONLY`**,因为缺少 ATR / RSI / ADX / 成交量等多维验证。

违反以上任何一条 → 输出 `decision: AVOID` 或 `REFERENCE_ONLY`,并在 `rejection_reasons` 字段列明。

-----

## v3.2 升级要点（相对 v3.1）

|模块         |v3.1           |v3.2                                                |
|-----------|---------------|----------------------------------------------------|
|方向偏向       |多空对称打分         |新增 `--bias short` / `--bias long` / `--bias neutral`|
|诱多反杀       |未识别            |新增 false-breakout reversal 形态识别 + 评分维度              |
|风控         |软建议            |7 条 Hard Rules 强制执行                                 |
|HTF 否决阈值   |confidence ≥ 50|提高到 ≥ 60(避免误杀)                                      |
|资金费率       |写死 0.0003/天    |默认拉实时,失败回退到 0.0003 fallback                         |
|入场策略       |三策略名称          |三策略 + 完整触发条件与优先级                                    |
|zone_status|三状态            |三状态 + 明确阈值定义                                        |
|回测         |描述提及但缺脚本       |标记为 roadmap,不在 v3.2 交付                              |

-----

## v3.1 升级要点（保留,相对 v3.0）

|模块     |v3.0                     |v3.1                                    |
|-------|-------------------------|----------------------------------------|
|入场策略   |仅 MA20 回踩 / 强趋势中点        |BREAKOUT / PULLBACK / RECLAIM 自动选 + 三档备选|
|止损     |仅 ATR×1.5                |ATR + swing 结构,取更保守者                    |
|HTF 反向 |仅扣分                      |高置信度反向一票否决 → AVOID                      |
|追踪止损   |静态 0.5R 回调               |Chandelier Exit(ATR×3 动态)               |
|手动模式   |输出完整 trade_plan + EXECUTE|输出 REFERENCE_ONLY,不出 take_profit        |
|BTC 对齐 |无                        |默认开启,反向高置信扣 -15                         |
|VWAP   |仅展示不评分                   |日内周期(5m/15m/1h)纳入评分                     |
|资金费率   |不计                       |按 timeframe + 目标 RR 估算持仓天数 + 成本         |
|in_zone|仅看上界                     |加 MA60 - 0.5×ATR 下界,跌破降级 CAUTION        |

-----

## 核心能力

|功能              |说明                                                                                       |
|----------------|-----------------------------------------------------------------------------------------|
|三均线趋势判定         |EMA20 / MA60 / MA120 排列 → 多头 / 空头 / 缠绕                                                   |
|14 维信号评分        |MA 排列 / 斜率 / ADX / RSI / MACD / 成交量 / 布林带 / K 线 / HTF / 市场状态 / 背离 / BTC / VWAP / **诱多反杀**|
|方向偏向模式          |`--bias short` 提高做多门槛,匹配空头偏向交易者                                                          |
|诱多反杀识别          |假突破 MA20/前高 → 长上影 → 快速回落 + RSI 顶背离                                                       |
|HTF 一票否决        |高级别趋势反向 + confidence ≥ 60 → 直接 AVOID                                                     |
|ATR + Swing 混合止损|取 ATR×1.5 与最近 swing ± 0.3×ATR 的更保守者                                                      |
|多入场策略           |BREAKOUT / PULLBACK / RECLAIM 自动选 + 三档备选                                                 |
|分批止盈            |默认 50%@3R / 30%@5R / 20%@8R(满足 3:1 硬规则)                                                  |
|Chandelier 追踪止损 |浮盈 1.5R 激活后,highest_high(22) - 3×ATR(22) 动态跟随                                            |
|保本止损            |浮盈达 1R 后移动止损至成本 + 手续费                                                                    |
|资金费率成本          |实时拉取(默认),失败回退 fallback 估算                                                                |
|爆仓价计算           |合约模式自动算强平价                                                                               |
|BTC 主导对齐        |非 BTC 标的默认检查 BTC 同周期趋势                                                                   |
|zone_status     |in_zone / await_pullback / trend_breakdown(阈值见下文)                                        |

-----

## 使用方法

脚本路径:`auto_dual_ma_skill.py`;消息面脚本:`btc_news_skill.py`

### 快捷模式（手动）

```bash
python3 auto_dual_ma_skill.py 82000 81500 81000 80000
```

顺序:`价格 MA20 MA60 [MA120]`,MA120 可省略。

> ⚠️ **手动模式恒定输出 `REFERENCE_ONLY`,不输出 EXECUTE**。

### 自动获取（联网）

```bash
# 默认 BTC/USDT 日线
python3 auto_dual_ma_skill.py

# ETH 4h + 多时间框架
python3 auto_dual_ma_skill.py -s ETH/USDT -t 4h --mtf

# SOL 日线,指定余额与杠杆
python3 auto_dual_ma_skill.py -s SOL/USDT -t 1d -b 10000 -l 5 --mtf

# 空头偏向 + 关闭 BTC 对齐(BTC 自身分析)
python3 auto_dual_ma_skill.py -s BTC/USDT -t 4h --bias short --no-btc-check
```

### 参数说明

|参数              |说明                                |默认值     |
|----------------|----------------------------------|--------|
|`-s`            |交易品种                              |BTC/USDT|
|`-t`            |时间周期(5m / 15m / 1h / 4h / 1d)     |1d      |
|`-r`            |单笔风险比例(硬上限 0.02)                  |0.02    |
|`-b`            |账户余额(USDT)                        |—       |
|`-l`            |杠杆倍数                              |1       |
|`-e`            |交易所                               |binance |
|`--bias`        |方向偏向(`short` / `long` / `neutral`)|neutral |
|`--mtf`         |多时间框架分析                           |off     |
|`--no-btc-check`|关闭 BTC 主导对齐                       |off     |
|`--config`      |自定义配置文件路径                         |—       |
|`--json`        |纯 JSON 输出                         |off     |
|`-v`            |详细日志                              |off     |

### 自定义配置（标准 JSON,ASCII 引号）

```json
{
  "atr_stop_multiplier": 2.0,
  "partial_tp_rr": [3.0, 5.0, 8.0],
  "min_avg_rr": 3.0,
  "trailing_atr_mult": 3.0,
  "trailing_lookback": 22,
  "swing_lookback": 20,
  "swing_buffer_atr": 0.3,
  "htf_veto_confidence": 60,
  "funding_rate_fallback_daily": 0.0003,
  "funding_rate_source": "exchange_realtime",
  "max_risk_per_trade": 0.02,
  "direction_bias": "neutral",
  "long_score_threshold_short_bias": 75,
  "exchanges_priority": ["okx", "binance"]
}
```

-----

## 方向偏向模式（v3.2 新增）

### `--bias short`（推荐给短期偏空交易者）

- 做空信号:正常打分,score ≥ 60 即可考虑 `EXECUTE`。
- 做多信号:提高门槛到 score ≥ 75,**且必须有诱多反杀以外的多重确认**。
- 用途:匹配 BTC 在死叉 + EMA20 下方的结构性弱势行情。

### `--bias long`

- 做多正常,做空提高到 75。

### `--bias neutral`（默认）

- 对称打分,无门槛差异。

-----

## 诱多反杀形态识别（v3.2 新增评分维度）

**定义**:价格上破关键阻力(MA20 / 前高 / 整数关口)后,在 1-3 根 K 线内快速回落,
形成长上影 + 收盘价低于突破位 + 量能放大 + RSI 顶背离 的反向信号。

### 触发条件（必须全部满足）

1. 近 3 根 K 线内出现突破阻力的 high(阻力 = MA20 上方 / 近 20 根 swing high / 整数关口)。
1. 突破 K 线或下一根 K 线的上影线 ≥ 实体的 2 倍。
1. 收盘价回落至阻力下方 0.3 × ATR 以上。
1. 突破 K 线成交量 ≥ 近 20 根均量 × 1.5。
1. RSI 顶背离(价格新高但 RSI 未创新高)。

### 评分（满足度按维度部分给分,满分 10）

|子条件           |分值|
|--------------|--|
|突破阻力 high     |2 |
|长上影 ≥ 2× 实体   |2 |
|收盘回落 > 0.3 ATR|2 |
|量能放大 ≥ 1.5×   |2 |
|RSI 顶背离       |2 |

≥ 6 分视为有效诱多反杀,进入做空候选;**score=10 且 `--bias short`,可在空头排列时直接给 `EXECUTE`**。

-----

## 入场策略详细逻辑（v3.2 完整化）

### 优先级:RECLAIM > PULLBACK > BREAKOUT

三策略互斥;同一根 K 线只触发一个。

### PULLBACK(回踩)— 趋势中段最优

- **多头**:价格回踩 EMA20 ± 0.3 × ATR,RSI 40-55,MA60 未跌破。
- **空头**:价格反抽 EMA20 ± 0.3 × ATR,RSI 45-60,MA60 未上破。
- 入场价:EMA20 处挂单或现价跟随。
- 止损:swing low/high ± 0.3 × ATR 与 ATR × 1.5 取更保守。

### RECLAIM(收回)— 反转最早

- **多头**:价格曾跌破 MA60,2-3 根内重新站回并伴随成交量放大。
- **空头**:价格曾上破 MA60,2-3 根内重新失守。
- 入场价:MA60 + 0.2 × ATR 确认。
- 止损:reclaim K 线低点/高点。
- 备选:若 reclaim 失败但 EMA20 仍提供支撑/阻力,降级为 PULLBACK。

### BREAKOUT(突破)— 趋势启动

- **多头**:连续 ≥ 5 根 K 线压缩(ATR 萎缩 30%+)后向上突破前高 + 量能 ≥ 1.5×。
- **空头**:同上,向下突破前低。
- 入场价:突破位 + 0.1 × ATR 防假突破。
- 止损:突破前的盘整区中点。
- ⚠️ **空头偏向模式下,做多 BREAKOUT 信号必须叠加 HTF 同向才执行**。

-----

## zone_status 状态机（v3.2 阈值定义）

针对多头结构:

|状态               |触发条件                                          |处理          |
|-----------------|----------------------------------------------|------------|
|`in_zone`        |MA60 ≤ price ≤ EMA20 + 0.5 × ATR,且 MA20 > MA60|可执行 PULLBACK|
|`await_pullback` |price > EMA20 + 0.5 × ATR(过远)                 |等回踩,不入场     |
|`trend_breakdown`|price < MA60 - 0.5 × ATR 或 EMA20 ≤ MA60       |多头作废,切换分析   |

空头结构对称镜像。

-----

## 信号评分（v3.2 新表,满分 110)

|维度            |分值      |说明                                    |
|--------------|--------|--------------------------------------|
|MA 排列         |20      |三线完美排列                                |
|MA 斜率         |10      |方向一致且陡峭                               |
|ADX           |10      |> 25 强趋势                              |
|RSI           |10      |合理区间(超买/超卖根据方向调整)                     |
|MACD          |10      |动能确认 + 柱体放大                           |
|成交量           |10      |≥ 1.5× 高量                             |
|布林带           |5       |价格位置合理                                |
|K 线形态         |5       |确认形态(吞没 / 锤子 / 黄昏星等)                  |
|高级别趋势         |15      |同向 +15;反向 + conf ≥ 60 → **AVOID 一票否决**|
|市场状态          |5       |趋势市加分,震荡市扣分                           |
|背离            |-15     |反向 RSI 背离扣分                           |
|BTC 主导        |±5 / -15|alt 标的同向 +5,反向高置信 -15                 |
|VWAP(仅日内)     |±5      |价格在 VWAP 有利侧 +5                       |
|**诱多反杀**(v3.2)|0-10    |仅在空头候选下激活,见上文                         |

最终归一化到 100。

-----

## 资金费率成本估算（v3.2 改进）

```
持仓天数估算 = target_rr_distance / (atr_per_day_estimate × bar_factor)
预估成本     = funding_rate_daily × leverage × holding_days
```

- **默认数据源**:`exchange_realtime` — 通过 ccxt 拉取所选交易所最新 funding rate
  (Bybit/Binance/OKX 永续合约)。
- **失败回退**:`funding_rate_fallback_daily = 0.0003`。
- 当成本 > 预期收益的 20% 时,触发警告并降级为 `REFERENCE_ONLY`。

-----

## 输出格式

```json
{
  "decision": "EXECUTE | REFERENCE_ONLY | AVOID",
  "direction": "LONG | SHORT | NEUTRAL",
  "score": 78,
  "score_normalized": 78,
  "trend": "BULLISH | BEARISH | RANGING",
  "zone_status": "in_zone | await_pullback | trend_breakdown",
  "entry_strategy": "RECLAIM | PULLBACK | BREAKOUT",
  "false_breakout_score": 0,
  "bias_mode": "neutral",
  "trade_plan": {
    "entry": 82000,
    "stop_loss": 80500,
    "stop_method": "swing_structure | atr | hybrid",
    "take_profits": [
      {"price": 86500, "size_pct": 50, "rr": 3.0},
      {"price": 89500, "size_pct": 30, "rr": 5.0},
      {"price": 94000, "size_pct": 20, "rr": 8.0}
    ],
    "avg_rr": 4.6,
    "position_size_usdt": 2000,
    "liquidation_price": 65600,
    "funding_cost_estimate_usdt": 4.2,
    "funding_source": "exchange_realtime"
  },
  "hard_rule_checks": {
    "min_rr_3to1": true,
    "risk_under_2pct": true,
    "post_mortem_complete": true,
    "no_hedging": true
  },
  "rejection_reasons": [],
  "warnings": []
}
```

-----

# BTC 最新消息面模块 v1.0

用于抓取并评估比特币最新消息面,把新闻催化剂转化为可读的交易辅助结论。
**它不是独立开仓系统**,必须结合价格结构、均线系统、成交量、支撑阻力和仓位风控使用。

## 触发场景

- “比特币最新消息面怎么样”
- “结合消息面分析 BTC”
- “有没有利好/利空”
- “ETF 资金流对 BTC 是利好吗”
- “美联储 / CPI / FOMC / 降息 / 加息 对比特币影响”
- “监管、SEC、黑客、巨鲸、矿工、清算、资金费率有没有风险”
- “现在做多/做空 BTC,消息面支持吗”

如果用户同时提供 K 线图或均线数据,应把本模块的 `news_bias` 与双/三均线交易系统结果合并,
而不是只看新闻。

## 脚本路径

```
btc_news_skill.py
```

依赖:默认只使用 Python 标准库。
可选:设置 `CRYPTOPANIC_TOKEN` 后会额外读取 CryptoPanic API。
默认不启用文件缓存。需要缓存时用 `--cache-minutes 15`。

## 快捷使用

```bash
# 最近 24 小时 BTC 消息面
python3 btc_news_skill.py --hours 24 --limit 10 --markdown

# JSON 输出便于程序合并
python3 btc_news_skill.py --hours 48 --limit 12

# 英文 Google News 源
python3 btc_news_skill.py --lang en --hours 24 --markdown

# 只用指定源
python3 btc_news_skill.py --sources google_news,cointelegraph_btc --hours 12 --markdown

# 启用 CryptoPanic
export CRYPTOPANIC_TOKEN="你的token"
python3 btc_news_skill.py --sources google_news,cryptopanic --hours 24

# 离线自测
python3 btc_news_skill.py --self-test
```

## 数据源

|源                        |说明                      |
|-------------------------|------------------------|
|Google News RSS          |聚合搜索,默认 `Bitcoin OR BTC`|
|Cointelegraph BTC RSS    |BTC 标签新闻                |
|CoinDesk RSS             |加密新闻                    |
|Bitcoin Magazine News RSS|Bitcoin Magazine        |
|Decrypt RSS              |Decrypt 加密新闻            |
|CryptoPanic              |可选,需 token              |

数据源可能因地区、反爬、RSS 改版失败。脚本会在 `source_errors` 中显示失败源,
**不能把失败源视为”没有新闻”**。

## 输出字段

```json
{
  "summary": {
    "news_bias": "BULLISH | BEARISH | MIXED | NEUTRAL",
    "news_score": -5.0,
    "risk_level": "LOW | MEDIUM | HIGH",
    "bullish_count": 0,
    "bearish_count": 0,
    "neutral_count": 0,
    "high_impact_count": 0,
    "top_categories": [],
    "interpretation": "消息面交易解读"
  },
  "items": [
    {
      "title": "新闻标题",
      "source": "来源",
      "published_at": "UTC时间",
      "age_hours": 1.5,
      "categories": ["ETF_INSTITUTION"],
      "relevance_score": 10,
      "impact_score": 2.8,
      "impact_label": "BULLISH",
      "matched_terms": ["etf", "inflows"]
    }
  ],
  "source_errors": {}
}
```

## 消息面分类

|分类              |含义                    |
|----------------|----------------------|
|ETF_INSTITUTION |ETF、机构买入/卖出、资金流入/流出   |
|MACRO_LIQUIDITY |美联储、CPI、PCE、美元、收益率、流动性|
|REGULATION      |SEC、法院、监管、批准/拒绝/诉讼    |
|SECURITY_RISK   |黑客、漏洞、被盗、交易所安全事件      |
|ONCHAIN_MINER   |巨鲸、矿工、链上转账、政府钱包、Mt.Gox|
|MARKET_STRUCTURE|清算、资金费率、未平仓、期权到期、波动率  |
|ADOPTION        |企业/国家/支付采用、储备资产叙事     |

## 与双/三均线系统合并规则

把 `news_bias` 当作技术信号的过滤器:

|技术面      |消息面                |处理                 |
|---------|-------------------|-------------------|
|多头排列 + 高分|BULLISH            |寻找回踩/突破确认后的顺势多头机会  |
|多头排列     |BEARISH 或 HIGH risk|不追多,降低仓位,等回踩和止跌确认  |
|空头排列     |BEARISH            |做空可信度提高,但仍需看支撑与清算风险|
|空头排列     |BULLISH            |谨慎追空,防止利好驱动反弹或逼空   |
|均线缠绕     |MIXED / NEUTRAL    |不强行开仓,等价格选择方向      |

### 建议回答结构

1. **消息面结论**:`news_bias / news_score / risk_level`
1. **关键新闻**:列 3-6 条最重要新闻,说明利好/利空原因
1. **主要催化剂**:ETF、宏观、监管、链上、市场结构分别说明
1. **与技术面关系**:和均线趋势、支撑阻力、成交量是否一致
1. **交易处理**:多单/空单/观望、仓位、止损、无效条件
1. **风险提示**:新闻可能滞后,不能替代止损

## 使用约束

- 不得把单条新闻直接等同于开仓信号。
- 如果 `source_errors` 很多,要明确说”数据源不完整”。
- 如果新闻大多来自同一源,要降低结论置信度。
- 如果 `news_bias = MIXED`,优先提示等待关键价位确认。
- 用户问”现在能不能做多/做空”,必须结合技术面;只有消息面时只能给”消息面支持/不支持”。
- 输出中必须说明”仅供参考,不构成投资建议”。

-----

# Roadmap(v3.2 暂未交付)

- `backtest.py`:历史回放引擎,统计胜率、平均 RR、最大回撤、各入场策略表现。
- 资金曲线与权益回撤可视化。
- 自动复盘报告生成器(读取 trade log → 输出 post-mortem markdown)。

> v3.1 文档中提及 `backtest.py` 系误标,v3.2 已移至 roadmap 待开发。
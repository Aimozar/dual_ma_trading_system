#!/usr/bin/env python3
"""
BTC 最新消息面 Skill v1.0

功能：
- 通过公开 RSS / Google News RSS / 可选 CryptoPanic API 抓取比特币相关新闻
- 去重、按时间排序、按关键词评估利好/利空/中性影响
- 输出结构化 JSON 或 Markdown，便于和双/三均线交易系统合并使用

设计原则：
- 默认只依赖 Python 标准库
- 不把单条新闻当交易信号；输出的是消息面偏向和风险提示
- 出错时返回明确的 source_errors，避免“假装抓到了新闻”
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "1.0.0"
USER_AGENT = (
    "Mozilla/5.0 (compatible; BTCNewsSkill/1.0; +https://openai.com; "
    "RSS reader for personal market analysis)"
)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "btc_news"

DEFAULT_RSS_SOURCES = {
    "google_news": "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}",
    "cointelegraph_btc": "https://cointelegraph.com/rss/tag/bitcoin",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "bitcoin_magazine_news": "https://bitcoinmagazine.com/news/feed",
    "decrypt": "https://decrypt.co/feed",
}

LANG_PROFILES = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
    "ja": {"hl": "ja", "gl": "JP", "ceid": "JP:ja"},
}

BTC_KEYWORDS = [
    "bitcoin", "btc", "比特币", "ビットコイン", "satoshi", "nakamoto",
    "spot bitcoin etf", "bitcoin etf", "btc etf", "现货比特币etf", "现货比特币 ETF",
]

MACRO_KEYWORDS = [
    "fed", "federal reserve", "fomc", "powell", "cpi", "pce", "inflation",
    "rate cut", "rate hike", "treasury yield", "bond yield", "dxy", "dollar",
    "liquidity", "qe", "qt", "tariff", "recession", "jobs report", "nonfarm",
    "美联储", "鲍威尔", "通胀", "降息", "加息", "美元", "收益率", "流动性", "非农",
    "日銀", "米連邦準備", "インフレ", "利下げ", "利上げ",
]

CATEGORY_KEYWORDS = {
    "ETF_INSTITUTION": [
        "etf", "blackrock", "fidelity", "ark", "grayscale", "ibit", "fbtc", "gbtc",
        "microstrategy", "strategy", "treasury company", "institutional", "inflows", "outflows",
        "现货 etf", "现货ETF", "贝莱德", "灰度", "机构", "资金流入", "资金流出", "增持",
    ],
    "MACRO_LIQUIDITY": MACRO_KEYWORDS,
    "REGULATION": [
        "sec", "cftc", "lawsuit", "sues", "court", "ban", "regulation", "regulator", "compliance",
        "approval", "approved", "reject", "rejected", "settlement", "监管", "起诉", "诉讼", "法院",
        "批准", "拒绝", "禁令", "合规", "调查",
    ],
    "SECURITY_RISK": [
        "hack", "hacked", "exploit", "stolen", "breach", "phishing", "rug", "attack",
        "黑客", "被盗", "攻击", "漏洞", "钓鱼", "安全事件",
    ],
    "ONCHAIN_MINER": [
        "miner", "miners", "mining", "hashrate", "halving", "whale", "wallet", "mt. gox", "mt gox",
        "government wallet", "exchange reserve", "on-chain", "链上", "矿工", "算力", "减半", "巨鲸", "钱包",
        "交易所储备", "政府钱包",
    ],
    "MARKET_STRUCTURE": [
        "liquidation", "liquidations", "short squeeze", "long squeeze", "funding rate", "open interest",
        "options expiry", "volatility", "futures", "perpetual", "清算", "爆仓", "逼空", "资金费率",
        "未平仓", "期权到期", "波动率", "合约",
    ],
    "ADOPTION": [
        "adoption", "payment", "payments", "reserve", "treasury", "company buys", "bank", "sovereign",
        "nation", "state", "el salvador", "采用", "支付", "储备", "国储", "企业买入", "银行",
    ],
}

POSITIVE_TERMS = [
    "approval", "approved", "greenlight", "wins", "win", "settlement", "dismissed", "inflows", "record inflows",
    "buys", "bought", "accumulates", "adds", "raises", "adopts", "adoption", "reserve", "treasury",
    "rate cut", "cuts rates", "dovish", "easing", "stimulus", "liquidity", "weaker dollar", "lower yields",
    "short squeeze", "breaks above", "rallies", "surges", "rebound", "recovers", "bullish",
    "批准", "胜诉", "和解", "流入", "创纪录流入", "买入", "增持", "采用", "储备", "降息",
    "鸽派", "宽松", "刺激", "流动性", "美元走弱", "收益率下降", "逼空", "突破", "反弹", "上涨", "利好",
]

NEGATIVE_TERMS = [
    "rejected", "rejects", "delay", "delayed", "outflows", "record outflows", "sells", "sold", "dump",
    "lawsuit", "sues", "charges", "ban", "crackdown", "probe", "investigation", "hack", "exploit", "stolen",
    "rate hike", "hikes rates", "hawkish", "higher yields", "stronger dollar", "hot cpi", "inflation hotter",
    "long liquidation", "liquidations", "sell-off", "plunges", "crash", "falls", "slumps", "bearish",
    "拒绝", "推迟", "流出", "创纪录流出", "卖出", "抛售", "起诉", "指控", "禁令", "打击",
    "调查", "黑客", "漏洞", "被盗", "加息", "鹰派", "收益率上升", "美元走强", "CPI高于预期",
    "多头爆仓", "清算", "暴跌", "下跌", "利空",
]

HIGH_IMPACT_TERMS = [
    "etf", "sec", "fomc", "fed", "cpi", "pce", "hack", "exploit", "blackrock", "microstrategy",
    "mt gox", "government wallet", "rate cut", "rate hike", "liquidation", "outflows", "inflows",
    "现货", "美联储", "降息", "加息", "黑客", "清算", "资金流入", "资金流出", "政府钱包",
]


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published_at: Optional[str]
    age_hours: Optional[float]
    summary: str
    categories: List[str]
    relevance_score: int
    impact_score: float
    impact_label: str
    matched_terms: List[str]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def clean_text(text: Optional[str], max_len: int = 500) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def lower_blob(*parts: str) -> str:
    return " ".join(p for p in parts if p).lower()


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def child_text(el: ET.Element, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for child in list(el):
        if strip_ns(child.tag) in wanted:
            return clean_text(child.text or "")
    return ""


def child_attr(el: ET.Element, name: str, attr: str) -> str:
    lname = name.lower()
    for child in list(el):
        if strip_ns(child.tag) == lname:
            return child.attrib.get(attr, "")
    return ""


def normalize_title(title: str) -> str:
    title = html.unescape(title).lower()
    title = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def contains_any(blob: str, terms: Iterable[str]) -> List[str]:
    hits = []
    for term in terms:
        t = term.lower()
        if t and t in blob:
            hits.append(term)
    return hits


def detect_categories(blob: str) -> List[str]:
    categories = []
    for cat, terms in CATEGORY_KEYWORDS.items():
        if contains_any(blob, terms):
            categories.append(cat)
    return categories or ["GENERAL_BTC"]


def score_item(title: str, summary: str, source: str, published: Optional[datetime], include_macro: bool) -> Tuple[int, float, str, List[str], List[str]]:
    blob = lower_blob(title, summary)
    btc_hits = contains_any(blob, BTC_KEYWORDS)
    macro_hits = contains_any(blob, MACRO_KEYWORDS)
    categories = detect_categories(blob)

    relevance = 0
    relevance += min(len(btc_hits) * 3, 9)
    if "bitcoin" in blob or "btc" in blob or "比特币" in blob:
        relevance += 6
    if macro_hits and include_macro:
        relevance += 3
    if "GENERAL_BTC" not in categories:
        relevance += 2
    relevance = min(relevance, 10)

    pos_hits = contains_any(blob, POSITIVE_TERMS)
    neg_hits = contains_any(blob, NEGATIVE_TERMS)
    high_hits = contains_any(blob, HIGH_IMPACT_TERMS)

    raw = 0.0
    raw += len(pos_hits) * 1.0
    raw -= len(neg_hits) * 1.0

    # 类别权重：ETF/宏观/监管/安全/市场结构对 BTC 短线影响更大。
    if any(c in categories for c in ["ETF_INSTITUTION", "MACRO_LIQUIDITY", "REGULATION", "SECURITY_RISK", "MARKET_STRUCTURE"]):
        raw *= 1.25
    if high_hits:
        raw *= 1.15

    # 清算类新闻常常是波动风险，不一定是方向；无明确多空时压低方向分。
    if "MARKET_STRUCTURE" in categories and not pos_hits and not neg_hits:
        raw = 0.0

    # 安全事件默认偏利空，除非标题明确说已追回/修复/胜诉。
    if "SECURITY_RISK" in categories and not pos_hits:
        raw -= 1.5

    # 监管没有明确批准/胜诉时偏风险。
    if "REGULATION" in categories and not pos_hits and neg_hits:
        raw -= 0.5

    # 时间衰减。
    if published:
        age_h = max((now_utc() - published).total_seconds() / 3600, 0)
        if age_h <= 6:
            recency = 1.0
        elif age_h <= 24:
            recency = 0.85
        elif age_h <= 72:
            recency = 0.65
        else:
            recency = 0.45
    else:
        recency = 0.55

    impact = max(min(raw * recency, 5.0), -5.0)
    if impact >= 1.5:
        label = "BULLISH"
    elif impact <= -1.5:
        label = "BEARISH"
    elif abs(impact) >= 0.6:
        label = "SLIGHT_BULLISH" if impact > 0 else "SLIGHT_BEARISH"
    else:
        label = "NEUTRAL"

    matched = list(dict.fromkeys(btc_hits + macro_hits + pos_hits + neg_hits + high_hits))[:18]
    return relevance, round(impact, 2), label, categories, matched


def cache_key(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def fetch_url(url: str, timeout: int = 12, cache_minutes: int = 15, retries: int = 1) -> Tuple[Optional[bytes], Optional[str], bool]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cpath = CACHE_DIR / f"{cache_key(url)}.bin"
    meta_path = CACHE_DIR / f"{cache_key(url)}.json"

    if cache_minutes > 0 and cpath.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if time.time() - meta.get("cached_at", 0) <= cache_minutes * 60:
                return cpath.read_bytes(), None, True
        except Exception:
            pass

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                cpath.write_bytes(data)
                meta_path.write_text(json.dumps({"cached_at": time.time(), "url": url}), encoding="utf-8")
                return data, None, False
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(1.0 + attempt)
    return None, last_err, False


def parse_rss(data: bytes, fallback_source: str, include_macro: bool, hours: int) -> List[NewsItem]:
    root = ET.fromstring(data)
    channel_title = ""
    for el in root.iter():
        if strip_ns(el.tag) == "channel":
            channel_title = child_text(el, "title")
            break
    source_name = clean_text(channel_title) or fallback_source

    nodes: List[ET.Element] = []
    for el in root.iter():
        if strip_ns(el.tag) in {"item", "entry"}:
            nodes.append(el)

    cutoff = now_utc() - timedelta(hours=hours)
    items: List[NewsItem] = []
    for node in nodes:
        title = child_text(node, "title")
        if not title:
            continue
        link = child_text(node, "link") or child_attr(node, "link", "href")
        summary = child_text(node, "description", "summary", "content", "encoded")
        pub_raw = child_text(node, "pubDate", "published", "updated", "date")
        published = parse_datetime(pub_raw)
        if published and published < cutoff:
            continue

        item_source = child_text(node, "source") or source_name
        relevance, impact, label, categories, matched = score_item(title, summary, item_source, published, include_macro)
        if relevance <= 0:
            continue
        age = None
        if published:
            age = round(max((now_utc() - published).total_seconds() / 3600, 0), 2)
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=item_source,
                published_at=published.isoformat() if published else None,
                age_hours=age,
                summary=summary,
                categories=categories,
                relevance_score=relevance,
                impact_score=impact,
                impact_label=label,
                matched_terms=matched,
            )
        )
    return items


def parse_cryptopanic(data: bytes, include_macro: bool, hours: int) -> List[NewsItem]:
    payload = json.loads(data.decode("utf-8"))
    cutoff = now_utc() - timedelta(hours=hours)
    results = payload.get("results", []) if isinstance(payload, dict) else []
    items: List[NewsItem] = []
    for row in results:
        title = clean_text(row.get("title", ""))
        if not title:
            continue
        published = parse_datetime(row.get("published_at"))
        if published and published < cutoff:
            continue
        source = clean_text((row.get("source") or {}).get("title") or "CryptoPanic")
        link = row.get("url") or row.get("slug") or ""
        summary = clean_text(row.get("metadata", {}).get("description", "") if isinstance(row.get("metadata"), dict) else "")
        relevance, impact, label, categories, matched = score_item(title, summary, source, published, include_macro)
        if relevance <= 0:
            continue
        age = round(max((now_utc() - published).total_seconds() / 3600, 0), 2) if published else None
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source,
                published_at=published.isoformat() if published else None,
                age_hours=age,
                summary=summary,
                categories=categories,
                relevance_score=relevance,
                impact_score=impact,
                impact_label=label,
                matched_terms=matched,
            )
        )
    return items


def dedupe_items(items: List[NewsItem]) -> List[NewsItem]:
    best: Dict[str, NewsItem] = {}
    for item in items:
        key = normalize_title(item.title)
        if not key:
            key = hashlib.md5((item.link or item.title).encode("utf-8")).hexdigest()
        old = best.get(key)
        if old is None:
            best[key] = item
            continue
        old_time = parse_datetime(old.published_at) if old.published_at else None
        new_time = parse_datetime(item.published_at) if item.published_at else None
        if (new_time or datetime.min.replace(tzinfo=timezone.utc)) > (old_time or datetime.min.replace(tzinfo=timezone.utc)):
            best[key] = item
    return list(best.values())


def source_urls(query: str, lang: str, selected_sources: List[str], hours: int) -> Dict[str, str]:
    profile = LANG_PROFILES.get(lang, LANG_PROFILES["en"])
    q = query.strip() or "Bitcoin OR BTC"
    # Google News 支持 when:24h 这种搜索约束；普通 RSS 源则由本地 cutoff 过滤。
    google_query = f"({q}) when:{hours}h"
    encoded_q = urllib.parse.quote(google_query)
    urls = {}
    for name, template in DEFAULT_RSS_SOURCES.items():
        if selected_sources and name not in selected_sources:
            continue
        if name == "google_news":
            urls[name] = template.format(query=encoded_q, **profile)
        else:
            urls[name] = template
    return urls


def collect_news(
    query: str = "Bitcoin OR BTC",
    hours: int = 48,
    limit: int = 12,
    lang: str = "zh",
    include_macro: bool = True,
    sources: Optional[List[str]] = None,
    cache_minutes: int = 15,
    timeout: int = 12,
    cryptopanic_token: Optional[str] = None,
    retries: int = 1,
) -> Dict[str, Any]:
    selected = sources or []
    urls = source_urls(query, lang, selected, hours)
    source_errors: Dict[str, str] = {}
    used_cache: Dict[str, bool] = {}
    all_items: List[NewsItem] = []

    for name, url in urls.items():
        data, err, cached = fetch_url(url, timeout=timeout, cache_minutes=cache_minutes, retries=retries)
        used_cache[name] = cached
        if err or not data:
            source_errors[name] = err or "empty response"
            continue
        try:
            all_items.extend(parse_rss(data, fallback_source=name, include_macro=include_macro, hours=hours))
        except Exception as exc:
            source_errors[name] = f"parse error: {type(exc).__name__}: {exc}"

    token = cryptopanic_token or os.getenv("CRYPTOPANIC_TOKEN")
    if token and (not selected or "cryptopanic" in selected):
        cp_url = (
            "https://cryptopanic.com/api/v1/posts/?"
            + urllib.parse.urlencode({"auth_token": token, "currencies": "BTC", "filter": "hot", "kind": "news"})
        )
        data, err, cached = fetch_url(cp_url, timeout=timeout, cache_minutes=cache_minutes, retries=retries)
        used_cache["cryptopanic"] = cached
        if err or not data:
            source_errors["cryptopanic"] = err or "empty response"
        else:
            try:
                all_items.extend(parse_cryptopanic(data, include_macro=include_macro, hours=hours))
            except Exception as exc:
                source_errors["cryptopanic"] = f"parse error: {type(exc).__name__}: {exc}"

    items = dedupe_items(all_items)
    items.sort(key=lambda x: ((x.published_at or ""), x.relevance_score, abs(x.impact_score)), reverse=True)
    # 先按时效排序，再选出高相关；最终列表兼顾影响分。
    items = sorted(items, key=lambda x: (x.relevance_score, abs(x.impact_score), -(x.age_hours or 9999)), reverse=True)[:limit]

    bullish = sum(1 for i in items if i.impact_score >= 0.6)
    bearish = sum(1 for i in items if i.impact_score <= -0.6)
    neutral = max(len(items) - bullish - bearish, 0)
    weighted_sum = sum(i.impact_score * max(i.relevance_score, 1) for i in items)
    weight = sum(max(i.relevance_score, 1) for i in items) or 1
    news_score = round(weighted_sum / weight, 2)

    if news_score >= 1.2 and bullish >= bearish:
        bias = "BULLISH"
    elif news_score <= -1.2 and bearish >= bullish:
        bias = "BEARISH"
    elif bullish > 0 and bearish > 0:
        bias = "MIXED"
    else:
        bias = "NEUTRAL"

    high_impact = [i for i in items if abs(i.impact_score) >= 2.5 or any(t.lower() in lower_blob(i.title, i.summary) for t in HIGH_IMPACT_TERMS)]
    risk_level = "LOW"
    if bias == "MIXED" or len(high_impact) >= 3:
        risk_level = "MEDIUM"
    if any(i.impact_label == "BEARISH" and abs(i.impact_score) >= 2.5 for i in items):
        risk_level = "HIGH"

    categories_count: Dict[str, int] = {}
    for item in items:
        for cat in item.categories:
            categories_count[cat] = categories_count.get(cat, 0) + 1

    interpretation = build_interpretation(bias, risk_level, bullish, bearish, neutral)

    return {
        "version": VERSION,
        "generated_at": now_utc().isoformat(),
        "query": query,
        "lookback_hours": hours,
        "language": lang,
        "summary": {
            "news_bias": bias,
            "news_score": news_score,
            "risk_level": risk_level,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "high_impact_count": len(high_impact),
            "top_categories": sorted(categories_count.items(), key=lambda x: x[1], reverse=True)[:5],
            "interpretation": interpretation,
        },
        "items": [asdict(i) for i in items],
        "source_errors": source_errors,
        "used_cache": used_cache,
        "disclaimer": "消息面评分仅用于辅助判断，不构成投资建议；必须结合价格结构、成交量、均线趋势和风险控制。",
    }


def build_interpretation(bias: str, risk_level: str, bullish: int, bearish: int, neutral: int) -> str:
    if bias == "BULLISH":
        return (
            "消息面偏多：更适合顺势找回踩/突破后的低风险多头机会，但不建议在急拉后追高；"
            "若技术面已经超买，应等回踩或量价确认。"
        )
    if bias == "BEARISH":
        return (
            "消息面偏空：多单应优先控制仓位和止损，避免在压力位追多；"
            "若价格跌破关键均线或支撑，短线反弹更应按减仓/防守处理。"
        )
    if bias == "MIXED":
        return (
            "消息面多空混杂：单靠新闻无法给出方向，技术面权重应提高；"
            "更适合轻仓、分批、等待关键价位确认。"
        )
    return (
        "消息面暂未形成明确催化：以盘面结构和均线系统为主，避免把普通新闻过度解读成交易信号。"
    )


def render_markdown(result: Dict[str, Any]) -> str:
    s = result["summary"]
    lines = []
    lines.append(f"# BTC 最新消息面快照 v{result['version']}")
    lines.append("")
    lines.append(f"生成时间(UTC)：{result['generated_at']}")
    lines.append(f"统计窗口：最近 {result['lookback_hours']} 小时")
    lines.append("")
    lines.append(
        f"**结论：{s['news_bias']} | 分数：{s['news_score']} | 风险：{s['risk_level']}**"
    )
    lines.append("")
    lines.append(s["interpretation"])
    lines.append("")
    lines.append("## 关键新闻")
    lines.append("")
    lines.append("| 时间 | 来源 | 影响 | 分类 | 标题 |")
    lines.append("|---|---|---:|---|---|")
    for item in result.get("items", []):
        age = item.get("age_hours")
        age_text = "未知" if age is None else f"{age:.1f}h前"
        cats = ",".join(item.get("categories", [])[:2])
        title = item.get("title", "")
        link = item.get("link") or ""
        if link:
            title_cell = f"[{title}]({link})"
        else:
            title_cell = title
        lines.append(
            f"| {age_text} | {item.get('source','')} | {item.get('impact_label','')} {item.get('impact_score',0)} | {cats} | {title_cell} |"
        )
    if result.get("source_errors"):
        lines.append("")
        lines.append("## 数据源错误")
        for name, err in result["source_errors"].items():
            lines.append(f"- {name}: {err}")
    lines.append("")
    lines.append(f"> {result.get('disclaimer','')}")
    return "\n".join(lines)


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Sample BTC News</title>
<item><title>Bitcoin ETF sees record inflows as BlackRock adds BTC exposure</title><link>https://example.com/a</link><pubDate>Tue, 12 May 2026 00:00:00 GMT</pubDate><description>Spot Bitcoin ETF inflows increased.</description></item>
<item><title>Bitcoin falls after hot CPI raises Fed rate hike worries</title><link>https://example.com/b</link><pubDate>Tue, 12 May 2026 01:00:00 GMT</pubDate><description>Macro liquidity pressure hits crypto.</description></item>
<item><title>Exchange reports hack but says Bitcoin reserves are safe</title><link>https://example.com/c</link><pubDate>Tue, 12 May 2026 02:00:00 GMT</pubDate><description>Security event creates market risk.</description></item>
</channel></rss>"""


def run_self_test() -> int:
    items = parse_rss(SAMPLE_RSS, "sample", include_macro=True, hours=2400)
    assert len(items) == 3, f"expected 3 items, got {len(items)}"
    labels = {i.title: i.impact_label for i in items}
    assert labels["Bitcoin ETF sees record inflows as BlackRock adds BTC exposure"] in {"BULLISH", "SLIGHT_BULLISH"}
    assert labels["Bitcoin falls after hot CPI raises Fed rate hike worries"] in {"BEARISH", "SLIGHT_BEARISH"}
    print("self-test passed")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"BTC 最新消息面抓取与影响评分 v{VERSION}",
        epilog="示例: python3 scripts/btc_news_skill.py --hours 24 --limit 10 --markdown",
    )
    parser.add_argument("--query", default="Bitcoin OR BTC", help="新闻搜索关键词，默认 Bitcoin OR BTC")
    parser.add_argument("--hours", type=int, default=48, help="回看小时数，默认 48")
    parser.add_argument("--limit", type=int, default=12, help="最多输出新闻条数，默认 12")
    parser.add_argument("--lang", choices=sorted(LANG_PROFILES.keys()), default="zh", help="Google News 地区语言，默认 zh")
    parser.add_argument("--sources", default="", help="逗号分隔的数据源名；留空为全部公开源")
    parser.add_argument("--no-macro", action="store_true", help="不纳入宏观关键词过滤")
    parser.add_argument("--cache-minutes", type=int, default=15, help="缓存分钟数，默认 15；0 为关闭")
    parser.add_argument("--timeout", type=int, default=12, help="单源请求超时秒数")
    parser.add_argument("--retries", type=int, default=1, help="单源失败重试次数，默认 1")
    parser.add_argument("--cryptopanic-token", default="", help="可选 CryptoPanic API token；也可设置环境变量 CRYPTOPANIC_TOKEN")
    parser.add_argument("--markdown", action="store_true", help="以 Markdown 输出；默认 JSON")
    parser.add_argument("--self-test", action="store_true", help="运行离线自测")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    result = collect_news(
        query=args.query,
        hours=max(args.hours, 1),
        limit=max(args.limit, 1),
        lang=args.lang,
        include_macro=not args.no_macro,
        sources=sources,
        cache_minutes=max(args.cache_minutes, 0),
        timeout=max(args.timeout, 3),
        cryptopanic_token=args.cryptopanic_token or None,
        retries=max(args.retries, 0),
    )
    if args.markdown:
        print(render_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

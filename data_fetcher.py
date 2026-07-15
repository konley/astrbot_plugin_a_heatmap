"""数据获取模块：从东方财富和同花顺拉取 A 股实时行情。"""

import asyncio
import time

import httpx

EASTMONEY_CLIST_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
EASTMONEY_ULIST_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
THS_UPDOWN_URL = "https://dq.10jqka.com.cn/fuyao/up_down_distribution/distribution/v2/realtime"

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://52etf.site/",
}

INDEX_SECIDS = "1.000001,0.399001,0.399006,1.000688"
INDEX_LABELS = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000688": "科创50",
}

# ── 优化：缩短 timeout，加大单页 size 减少分页数 ──
FETCH_TIMEOUT = 8.0           # 15s → 8s
PAGE_SIZE = 200               # 100 → 200，分页数减半
MAX_PAGES = 30                # 60 → 30，配合 PAGE_SIZE=200 足够

# ── 优化：简单内存缓存，30 秒 TTL ──
_CACHE_TTL = 30
_cache: dict = {}              # key -> (timestamp, data)


def _num(v, default=0.0):
    try:
        return float(v) if v not in (None, "", "-") else default
    except (ValueError, TypeError):
        return default


def _cache_get(key):
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL:
        return None
    return data


def _cache_set(key, data):
    _cache[key] = (time.time(), data)


async def fetch_all_a_stocks(client: httpx.AsyncClient) -> list[dict]:
    """分页拉取全 A 股行情，返回股票列表。"""
    cached = _cache_get("stocks")
    if cached is not None:
        return cached

    all_stocks = []
    page = 1
    while True:
        url = (
            f"{EASTMONEY_CLIST_URL}"
            f"?pn={page}&pz={PAGE_SIZE}&po=1&np=1&fltt=2&invt=2&fid=f3"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
            "&fields=f2,f3,f4,f6,f12,f13,f14,f20,f100"
        )
        resp = await client.get(url, headers=COMMON_HEADERS, timeout=FETCH_TIMEOUT)
        data = resp.json()
        data_body = data.get("data") or {}
        items = data_body.get("diff") or []
        if not items:
            break
        for it in items:
            code = it.get("f12", "")
            name = it.get("f14", "")
            mcap = _num(it.get("f20", 0))
            if not code or not name or mcap <= 0:
                continue
            all_stocks.append({
                "code": code,
                "name": name,
                "pct": _num(it.get("f3", 0)),
                "price": _num(it.get("f2", 0)),
                "amount": _num(it.get("f6", 0)),
                "mcap": mcap,
                "industry": it.get("f100", "其他") or "其他",
            })
        page += 1
        if page > MAX_PAGES:
            break

    _cache_set("stocks", all_stocks)
    return all_stocks


async def fetch_market_indices(client: httpx.AsyncClient) -> dict:
    """拉取主要指数行情。"""
    cached = _cache_get("indices")
    if cached is not None:
        return cached

    url = f"{EASTMONEY_ULIST_URL}?fltt=2&fields=f2,f3,f4,f12,f14&secids={INDEX_SECIDS}"
    resp = await client.get(url, headers=COMMON_HEADERS, timeout=FETCH_TIMEOUT)
    data = resp.json()
    result = {}
    for it in (data.get("data") or {}).get("diff", []):
        code = it.get("f12", "")
        result[code] = {
            "name": INDEX_LABELS.get(code, it.get("f14", code)),
            "price": _num(it.get("f2", 0)),
            "pct": _num(it.get("f3", 0)),
        }

    _cache_set("indices", result)
    return result


async def fetch_up_down_count(client: httpx.AsyncClient) -> dict:
    """拉取涨跌家数统计（同花顺）。"""
    cached = _cache_get("up_down")
    if cached is not None:
        return cached

    resp = await client.get(THS_UPDOWN_URL, headers=COMMON_HEADERS, timeout=FETCH_TIMEOUT)
    data = resp.json()
    d = data.get("data", {})
    result = {
        "up": d.get("up", 0),
        "down": d.get("down", 0),
        "equal": d.get("equal", 0),
        "limit_up": d.get("limit_up", 0),
        "limit_down": d.get("limit_down", 0),
    }

    _cache_set("up_down", result)
    return result


async def fetch_all() -> tuple[list[dict], dict, dict]:
    """一次性拉取全部数据，返回 (stocks, indices, up_down)。"""
    async with httpx.AsyncClient() as client:
        stocks, indices, up_down = await _fetch_all_concurrent(client)
        return stocks, indices, up_down


async def _fetch_all_concurrent(client: httpx.AsyncClient) -> tuple[list[dict], dict, dict]:
    """并发拉取三组数据，单个失败不影响其他。"""
    results = await asyncio.gather(
        fetch_all_a_stocks(client),
        fetch_market_indices(client),
        fetch_up_down_count(client),
        return_exceptions=True,
    )

    stocks = results[0] if not isinstance(results[0], Exception) else []
    indices = results[1] if not isinstance(results[1], Exception) else {}
    up_down = results[2] if not isinstance(results[2], Exception) else {}

    if isinstance(results[0], Exception):
        from astrbot.api import logger
        logger.error(f"[a_heatmap] 股票数据获取失败: {results[0]}")
    if isinstance(results[1], Exception):
        from astrbot.api import logger
        logger.error(f"[a_heatmap] 指数数据获取失败: {results[1]}")
    if isinstance(results[2], Exception):
        from astrbot.api import logger
        logger.error(f"[a_heatmap] 涨跌统计获取失败: {results[2]}")

    return stocks, indices, up_down

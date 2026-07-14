"""数据获取模块：从东方财富和同花顺拉取 A 股实时行情。"""

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


def _num(v, default=0.0):
    try:
        return float(v) if v not in (None, "", "-") else default
    except (ValueError, TypeError):
        return default


async def fetch_all_a_stocks(client: httpx.AsyncClient) -> list[dict]:
    """分页拉取全 A 股行情，返回股票列表。"""
    all_stocks = []
    page = 1
    while True:
        url = (
            f"{EASTMONEY_CLIST_URL}"
            f"?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
            "&fields=f2,f3,f4,f6,f12,f13,f14,f20,f100"
        )
        resp = await client.get(url, headers=COMMON_HEADERS, timeout=15)
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
        if page > 60:
            break
    return all_stocks


async def fetch_market_indices(client: httpx.AsyncClient) -> dict:
    """拉取主要指数行情。"""
    url = f"{EASTMONEY_ULIST_URL}?fltt=2&fields=f2,f3,f4,f12,f14&secids={INDEX_SECIDS}"
    resp = await client.get(url, headers=COMMON_HEADERS, timeout=15)
    data = resp.json()
    result = {}
    for it in (data.get("data") or {}).get("diff", []):
        code = it.get("f12", "")
        result[code] = {
            "name": INDEX_LABELS.get(code, it.get("f14", code)),
            "price": _num(it.get("f2", 0)),
            "pct": _num(it.get("f3", 0)),
        }
    return result


async def fetch_up_down_count(client: httpx.AsyncClient) -> dict:
    """拉取涨跌家数统计（同花顺）。"""
    resp = await client.get(THS_UPDOWN_URL, headers=COMMON_HEADERS, timeout=15)
    data = resp.json()
    d = data.get("data", {})
    return {
        "up": d.get("up", 0),
        "down": d.get("down", 0),
        "equal": d.get("equal", 0),
        "limit_up": d.get("limit_up", 0),
        "limit_down": d.get("limit_down", 0),
    }


async def fetch_all() -> tuple[list[dict], dict, dict]:
    """一次性拉取全部数据，返回 (stocks, indices, up_down)。"""
    async with httpx.AsyncClient() as client:
        stocks, indices, up_down = await _fetch_all_concurrent(client)
        return stocks, indices, up_down


async def _fetch_all_concurrent(client: httpx.AsyncClient) -> tuple[list[dict], dict, dict]:
    import asyncio

    stocks_task = asyncio.create_task(fetch_all_a_stocks(client))
    indices_task = asyncio.create_task(fetch_market_indices(client))
    updown_task = asyncio.create_task(fetch_up_down_count(client))

    stocks = await stocks_task
    indices = await indices_task
    up_down = await updown_task
    return stocks, indices, up_down

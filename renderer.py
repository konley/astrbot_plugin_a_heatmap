"""热力图渲染模块：matplotlib + squarify 渲染 A 股 treemap。"""

import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import squarify
from matplotlib.patches import Rectangle

# ── 画布尺寸常量 (data coords) ──
CANVAS_W = 1600
CANVAS_H = 950
TOP_BAR_H = 70

# ── 中文字体 ──
_cn_font = None


def _get_cn_font():
    global _cn_font
    if _cn_font is not None:
        return _cn_font
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            _cn_font = fm.FontProperties(fname=p)
            return _cn_font
    _cn_font = fm.FontProperties()
    return _cn_font


def _pct_to_color(pct: float) -> str:
    if pct > 9.5:
        return "#E80000"
    elif pct > 5:
        return "#F23030"
    elif pct > 2:
        return "#F26230"
    elif pct > 0:
        return "#F5915F"
    elif pct == 0:
        return "#808080"
    elif pct > -2:
        return "#5BB678"
    elif pct > -5:
        return "#3FAE5A"
    elif pct > -9.5:
        return "#1B9E45"
    else:
        return "#008000"


def render_treemap(
    stocks: list[dict],
    indices: dict,
    up_down: dict,
    out_path: str,
    max_industries: int = 35,
    max_stocks_per_industry: int = 50,
    dpi: int = 130,
) -> str:
    """渲染 A 股热力图并保存到 out_path。"""
    font = _get_cn_font()

    # ── 按行业分组 ──
    industries: dict[str, dict] = {}
    for s in stocks:
        ind = s["industry"]
        if ind not in industries:
            industries[ind] = {"stocks": [], "total_mcap": 0.0}
        industries[ind]["stocks"].append(s)
        industries[ind]["total_mcap"] += s["mcap"]

    sorted_inds = sorted(industries.items(), key=lambda x: -x[1]["total_mcap"])
    top_inds = sorted_inds[:max_industries]

    # ── 行业级 treemap ──
    ind_sizes = [ind_data["total_mcap"] / 1e8 for _, ind_data in top_inds]
    treemap_area_w = CANVAS_W
    treemap_area_h = CANVAS_H - TOP_BAR_H
    ind_rects = squarify.normalize_sizes(ind_sizes, treemap_area_w, treemap_area_h)
    ind_rects = squarify.squarify(ind_rects, 0, TOP_BAR_H, treemap_area_w, treemap_area_h)

    # ── 创建画布 ──
    fig, ax = plt.subplots(figsize=(16, 9.5), dpi=dpi)
    ax.set_xlim(0, CANVAS_W)
    ax.set_ylim(0, CANVAS_H)
    ax.invert_yaxis()
    ax.axis("off")

    # ════════════════════════════════════════════
    # 顶部信息栏 — 全部用 ax data 坐标
    # ════════════════════════════════════════════
    idx_codes = ["000001", "399001", "399006", "000688"]

    y_line1 = 15
    y_line2 = 42
    y_sep_mid = 28
    y_sep_bot = 68

    for i, code in enumerate(idx_codes):
        if code not in indices:
            continue
        idx = indices[code]
        pct = idx["pct"]
        if pct > 0:
            color = "#CC0000"
            arrow = "▲"
        elif pct < 0:
            color = "#008800"
            arrow = "▼"
        else:
            color = "#666666"
            arrow = "—"

        name = idx["name"]
        cx = (i + 0.5) / 4.0 * CANVAS_W
        text = f"{name}  {idx['price']:.2f}  {arrow}{pct:+.2f}%"
        ax.text(cx, y_line1, text, ha="center", va="center",
                fontproperties=font, fontsize=11, color=color, fontweight="bold")

    ax.plot([CANVAS_W * 0.05, CANVAS_W * 0.95], [y_sep_mid, y_sep_mid],
            color="#DDDDDD", linewidth=0.8)

    up = up_down.get("up", 0)
    down = up_down.get("down", 0)
    lu = up_down.get("limit_up", 0)
    ld = up_down.get("limit_down", 0)
    eq = up_down.get("equal", 0)

    stats_items = [
        (f"上涨 {up}", "#CC0000"),
        (f"下跌 {down}", "#008800"),
        (f"平盘 {eq}", "#888888"),
        (f"涨停 {lu}", "#E80000"),
        (f"跌停 {ld}", "#008000"),
    ]
    n = len(stats_items)
    for i, (text, color) in enumerate(stats_items):
        cx = (i + 0.5) / n * CANVAS_W
        ax.text(cx, y_line2, text, ha="center", va="center",
                fontproperties=font, fontsize=10, color=color, fontweight="bold")

    ax.plot([0, CANVAS_W], [y_sep_bot, y_sep_bot],
            color="#333333", linewidth=1.5)

    # ════════════════════════════════════════════
    # 行业 treemap + 个股渲染
    # ════════════════════════════════════════════
    for (ind_name, ind_data), rect in zip(top_inds, ind_rects):
        x, y, dx, dy = rect["x"], rect["y"], rect["dx"], rect["dy"]

        ind_stocks = sorted(ind_data["stocks"], key=lambda s: -s["mcap"])
        ind_stocks = ind_stocks[:max_stocks_per_industry]

        stock_sizes = [s["mcap"] / 1e8 for s in ind_stocks]
        if sum(stock_sizes) == 0:
            continue

        sub_rects = squarify.normalize_sizes(stock_sizes, dx, dy)
        sub_rects = squarify.squarify(sub_rects, x, y, dx, dy)

        total_mc = sum(s["mcap"] for s in ind_stocks)
        ind_pct = sum(s["pct"] * s["mcap"] for s in ind_stocks) / total_mc if total_mc > 0 else 0
        ind_color = _pct_to_color(ind_pct)

        # 行业标题条带
        bar_h = 18 if dy > 50 else (12 if dy > 30 else 0)
        if bar_h > 0:
            ax.add_patch(Rectangle((x, y), dx, bar_h,
                                   facecolor="#1A1A2E", edgecolor="none", zorder=5))
            ax.add_patch(Rectangle((x, y), 4, bar_h,
                                   facecolor=ind_color, edgecolor="none", zorder=6))
            label_text = f" {ind_name}  {ind_pct:+.2f}%"
            fs = 9 if dx > 80 else (7.5 if dx > 40 else 6)
            ax.text(x + 6, y + bar_h / 2, label_text,
                    ha="left", va="center",
                    fontproperties=font, fontsize=fs,
                    color="#FFFFFF", fontweight="bold", zorder=7)

        # 个股方块
        for stock, sr in zip(ind_stocks, sub_rects):
            color = _pct_to_color(stock["pct"])
            sx, sy, sdx, sdy = sr["x"], sr["y"], sr["dx"], sr["dy"]

            if sdx < 2 or sdy < 2:
                continue

            ax.add_patch(Rectangle((sx, sy), sdx, sdy,
                                   facecolor=color, edgecolor="#FFFFFF",
                                   linewidth=0.4, zorder=2))

            cx_s = sx + sdx / 2
            cy_s = sy + sdy / 2

            if sdx > 25 and sdy > 18:
                name = stock["name"]
                pct_str = f'{stock["pct"]:+.1f}%'
                fs_name = min(8, max(5, sdx / 10))
                fs_pct = fs_name * 0.85
                if sdy > 30:
                    ax.text(cx_s, cy_s - fs_name * 0.6, name,
                            ha="center", va="center",
                            fontproperties=font, fontsize=fs_name,
                            color="#FFFFFF", fontweight="bold", zorder=3)
                    ax.text(cx_s, cy_s + fs_name * 0.8, pct_str,
                            ha="center", va="center",
                            fontproperties=font, fontsize=fs_pct,
                            color="#FFFFFF", zorder=3)
                else:
                    ax.text(cx_s, cy_s, f"{name} {pct_str}",
                            ha="center", va="center",
                            fontproperties=font, fontsize=fs_name,
                            color="#FFFFFF", fontweight="bold", zorder=3)
            elif sdx > 14 and sdy > 10:
                fs = min(6, max(4, sdx / 12))
                ax.text(cx_s, cy_s, stock["name"],
                        ha="center", va="center",
                        fontproperties=font, fontsize=fs,
                        color="#FFFFFF", fontweight="bold", zorder=3)
            elif sdx > 8 and sdy > 6:
                fs = min(4.5, max(3, sdx / 14))
                short = stock["name"][:2]
                ax.text(cx_s, cy_s, short,
                        ha="center", va="center",
                        fontproperties=font, fontsize=fs,
                        color="#FFFFFF", zorder=3)

        # 行业块外边框
        ax.add_patch(Rectangle((x, y), dx, dy,
                               facecolor="none", edgecolor="#333333",
                               linewidth=1.5, zorder=4))

    # ════════════════════════════════════════════
    # 底部水印
    # ════════════════════════════════════════════
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    fig.text(0.01, 0.008, now_str, ha="left", va="bottom",
             fontproperties=font, fontsize=8, color="#888888")
    fig.text(0.99, 0.008, "Powered by konley", ha="right", va="bottom",
             fontproperties=font, fontsize=8, color="#888888")

    plt.subplots_adjust(left=0.01, right=0.99, top=0.97, bottom=0.03)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight",
                facecolor="#FFFFFF", edgecolor="none")
    plt.close(fig)
    return out_path

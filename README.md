# A股热力图 (astrbot_plugin_a_heatmap)

> 一句话生成 A 股大盘热力图：默认 **高清全屏截取** [52etf.site](https://52etf.site/) 页面（含侧栏）；官网「截图分享」与画布截图仅作兜底。本地 matplotlib 自绘保留为「热力图2」。支持定时推送。

## 功能

- **热力图**：Playwright 打开 52etf.site，**默认高 DPI 视口全屏截图**（`device_scale_factor` 默认 3，约 4200×2700）；失败再兜底「截图分享」/ `#treemap`
- **热力图2**：东方财富 + 同花顺数据，本地 treemap 自绘（红涨绿跌，行业分组）
- 多触发词、斜杠忽略、冷却、管理员限制
- Cron 定时推送到指定群/私聊（默认同官方图）

## 使用方法

### 手动触发

```
热力图          # 52etf 高清全屏截图（约 15~30 秒）
/热力图
热力图2         # 本地自绘
```

### 定时推送

WebUI 插件配置中开启 `schedule_enable`，配置 Cron、`schedule_targets`，可选 `schedule_mode`：

| schedule_mode | 含义 |
|---------------|------|
| `site`（默认） | 52etf 高清视口截图 |
| `legacy` | 本地自绘 |

常用 Cron：

| 表达式 | 含义 |
|--------|------|
| `0 15 * * 1-5` | 每个工作日 15:00 收盘推送 |
| `0 9,15 * * 1-5` | 工作日 9:00 和 15:00 |
| `30 14 * * 1-5` | 工作日 14:30 |

## 服务器依赖（官方图）

生产环境需在 **AstrBot 使用的 Python** 中具备：

```bash
# 示例（uv 安装的 astrbot）
/root/.local/share/uv/tools/astrbot/bin/python -m pip install playwright
/root/.local/share/uv/tools/astrbot/bin/python -m playwright install chromium
# 无桌面 Linux 可能还需要：playwright install-deps chromium
```

说明：

- 使用 headless Chromium；ARM64（如 Oracle aarch64）需安装对应架构浏览器包
- 单次导出约 20 秒、图片约 1~2MB；插件内 **浏览器复用 + 全局锁**，避免并发多开
- 建议 `cooldown >= 20`
- 内存紧张的 2C/小内存机器请控制触发频率

## WebUI 主要配置

| 配置项 | 默认 | 说明 |
|--------|------|------|
| command_keywords | 热力图 | 52etf 页面截图触发词 |
| command_keywords_legacy | 热力图2 | 自绘触发词 |
| site_ready_wait_ms | 8000 | 打开页面后等待渲染 |
| fallback_to_legacy_on_site_fail | false | 官方失败是否降级自绘 |
| schedule_mode | site | 定时推送图源 |
| cooldown | 30 | 冷却秒数 |

自绘相关：`max_industries` / `max_stocks_per_industry` / `dpi` 仅「热力图2」生效。

## 数据与图源

| 路径 | 来源 |
|------|------|
| 热力图 | https://52etf.site/ 高清视口截图（失败时截图分享 / 画布） |
| 热力图2 | 东财 `push2delay.eastmoney.com`、同花顺涨跌统计 |

图源归属 52etf.site，请合理控制调用频率。

## 依赖

- playwright
- matplotlib
- squarify
- httpx
- apscheduler

## 作者

konley

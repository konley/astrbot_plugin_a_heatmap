# A股热力图 (astrbot_plugin_a_heatmap)

> A股大盘实时热力图，一句话生成，支持定时推送。

## 功能

- 发送关键词（默认"热力图"）生成 A 股全市场 treemap 热力图
- 红涨绿跌，按行业板块分组，面积=市值
- 顶部显示上证/深证/创业板/科创50 指数 + 涨跌家数统计
- 支持多触发词、斜杠忽略、冷却限制、管理员限制
- 支持 Cron 定时推送到指定群/私聊

## 使用方法

### 手动触发

```
热力图
/热力图
#热力图
```

### 定时推送

在 WebUI 插件配置中开启 `schedule_enable`，配置 Cron 表达式和推送目标即可。

常用 Cron 示例：

| 表达式 | 含义 |
|--------|------|
| `0 15 * * 1-5` | 每个工作日 15:00 收盘推送 |
| `0 9,15 * * 1-5` | 工作日 9:00 和 15:00 各推一次 |
| `30 14 * * 1-5` | 工作日 14:30 推送 |

## WebUI 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| command_keywords | string | 热力图 | 触发词，逗号分隔支持多个 |
| ignore_slash | bool | true | 忽略 / 或 # 前缀 |
| admin_only | bool | false | 仅管理员可调用 |
| cooldown | int | 30 | 冷却时间（秒） |
| max_industries | int | 35 | 显示前 N 个行业 |
| max_stocks_per_industry | int | 50 | 每行业最多个股数 |
| dpi | int | 130 | 图片清晰度 |
| schedule_enable | bool | false | 开启定时推送 |
| schedule_cron | string | 0 15 * * 1-5 | Cron 表达式 |
| schedule_targets | list | [] | 推送目标（群号/QQ号） |
| schedule_target_type | string | group | group 或 private |
| schedule_platform | string | | 平台实例 ID，留空自动检测 |

## 数据来源

- 东方财富 `push2delay.eastmoney.com` — 全 A 股行情 + 指数
- 同花顺 `dq.10jqka.com.cn` — 涨跌家数统计

## 依赖

- matplotlib
- squarify
- httpx
- apscheduler

## 作者

konley

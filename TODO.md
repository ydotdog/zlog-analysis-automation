# Known Issues & Optimization TODOs

## Architecture Changes

### 架构重构：模型自主搜索 (已完成)
- **旧**：Scrape → 异动检测(硬编码阈值) → 新闻搜索(独立 CLI 调用) → 生成复盘(无工具)
- **新**：Scrape → 生成复盘(带 WebSearch/WebFetch，AI 自主决定搜什么)
- 删除 `search_news()`、`parse_anomaly_tickers()`、`_detect_topact_columns()`
- 删除硬编码阈值 `ANOMALY_VR_THRESHOLD`、`ANOMALY_CHG_THRESHOLD`、`MAX_NEWS_TICKERS`
- `generate_daily_review()` 加 `allowed_tools="WebSearch WebFetch"`，模型自主搜索
- 搜索指引通过 CLAUDE.md system prompt 传达，不在代码中硬编码

## Resolved

### ~~数据裁剪策略丢失关键信息~~ (已修复)
- 完整数据直接传给模型，不再截断

### ~~无数据校验~~ (已修复)
- `validate_scrape_data()` 校验 zlog/MS/sector/TOPACT 格式和完整性

### ~~无容错和重试机制~~ (已修复)
- `run_claude_cli` 失败时自动重试（指数退避）
- `run_daily` 复用已有 `scrape_*.json` checkpoint
- 数据为空时自动截图

### ~~Claude CLI exit=1 的 workaround~~ (已修复)
- 不再丢弃有效短输出，应在 CLI 升级后移除 workaround

### ~~Dashboard URL 使用错误时区~~ (已修复)
- Dashboard URL 与 zlog/sector 一样使用北京日期，之前代码错误地传入纽约日期
- 之前未暴露问题是因为网站对非交易日自动回退到最近交易日

## P3 - Performance

### 数据采集可并行化
- zlog、sector、dashboard 三个页面互相独立
- 当前串行采集 ~50 秒，可用多 tab/多 context 并行压缩到 ~25 秒

## Notes

- TOPACT 历史列（0321, 0314 等）是 BJ 周六日期 = NY 周五收盘的周度快照
- 所有 Chronoweb 页面 URL（zlog/sector/dashboard）均使用北京日期
- 复盘生成耗时约 13-20 分钟（Claude opus），timeout 设为 9000 秒（150 分钟）
- **重要**：CLAUDE.md 需要添加工具使用指引（WebSearch/WebFetch 搜索策略），否则模型可能不主动搜索

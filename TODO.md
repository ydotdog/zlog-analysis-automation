# Known Issues & Optimization TODOs

## Resolved

### ~~1. 新闻搜索功能失效~~ (排查完成)
- `search_news()` 间歇性返回 ~30 字符（如 "I cannot perform web searches."）
- **排查结论**：CLI 基础设施正常（`--allowedTools WebSearch` 在 `-p` 模式下可用），无法本地复现
- 最可能原因：WebSearch `side_query` API 间歇性 rate limit（Issue #27074）
- **已加固**：输出 <200 字符时记录完整内容、结果长度校验、重试机制（max_retries=2, 指数退避）

### ~~2. 异动检测逻辑粗糙~~ (已修复)
- 重写 `parse_anomaly_tickers()`：自动检测 TOPACT 列头（VR/Chg），按列位置精确解析
- Fallback 到第 11 列（index 10）= VR
- 同时从 TOPACT 提取 Chg 异动（之前只从 sector_details 提取）

### ~~3. 数据裁剪策略丢失关键信息~~ (已修复)
- 删除 `_trim_sector_details`、`_trim_topact`、`_trim_ms_table`
- 完整数据直接传给模型，不再截断

### ~~4. 无数据校验~~ (已修复)
- 新增 `validate_scrape_data()`，采集后校验：
  - zlog 含 Sentiment/Yes/Lit 关键字段
  - MS 表含表格格式字符
  - sector 板块数量 ≥ 20
  - TOPACT 数据行 ≥ 10 且含 VR 列头
- 校验失败记 warning（不中断流程）

### ~~5. 无容错和重试机制~~ (已修复)
- `run_claude_cli` 失败时自动重试（指数退避 30s/60s/120s）
- `run_daily` 复用已有 `scrape_*.json` checkpoint：采集成功但复盘失败 → 下次跳过采集
- 数据为空时自动截图（zlog_empty.png / sector_empty.png / dashboard_empty.png）

### ~~6. Claude CLI exit=1 的 workaround~~ (已修复)
- 输出门槛从 `len(output) > 100` 改为 `if output:`，不再丢弃有效短输出
- 应在 CLI 升级修复 `signature_delta` bug 后移除整个 workaround

## P3 - Performance

### 7. 数据采集可并行化
- zlog、sector、dashboard 三个页面互相独立
- 当前串行采集 ~50 秒，可用多 tab/多 context 并行压缩到 ~25 秒
- 对 cron 不紧急，但工程上更优

## Notes

- TOPACT 历史列（0321, 0314 等）是 BJ 周六日期 = NY 周五收盘的周度快照
- CLAUDE.md 中关于历史列日期的规则描述不够精确（说是 NY 日期，实际是 BJ 日期）
- 复盘生成耗时约 13-20 分钟（Claude opus），timeout 设为 9000 秒（150 分钟）

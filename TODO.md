# Known Issues & Optimization TODOs

## P0 - Broken Features

### 1. 新闻搜索功能失效
- `search_news()` 通过 Claude CLI + WebSearch 搜索异动标的新闻
- 连续多次返回仅 30 字符（应返回 1000+ 字符的详细新闻汇总）
- CLAUDE.md 明确要求对极端异动标的搜索新闻，此功能不工作直接影响复盘质量
- **需排查**：Claude CLI 的 `--allowedTools WebSearch WebFetch` 在 subprocess 调用中是否正常工作

## P1 - Data Quality

### 2. 异动检测逻辑粗糙
- `parse_anomaly_tickers()` 通过正则匹配 `│` 分隔的 TOPACT 文本
- 假设 VR 值在"带 `*` 或 `+` 前缀的 cell"里，实际 TOPACT 表列位置固定（第11列=VR）
- 应按列位置精确解析，而非字符串猜测
- 阈值 `VR>200, |Chg|>15%` 可能遗漏重要异动（如 VR=150 但连续出现的标的）

### 3. 数据裁剪策略丢失关键信息
- `_trim_sector_details()`: 每板块只留前 5 只，第 6 位可能恰好是当日异动标的
- `_trim_topact()`: 按 `\n\n` 分割只保留第一页，第二三页标的完全丢弃
- **改进方向**：按重要性筛选（VR 极端值 > Chg 极端值 > 连续出现 > 大市值），而非按位置截断

### 4. 无数据校验
- 采集完直接喂给模型，未验证数据完整性：
  - zlog_text 是否包含 Sentiment/Yes/Lit 字段
  - sector 25 个板块是否都提取了个股
  - TOPACT 列头是否匹配预期格式
- 网站改版或数据异常时会静默生成垃圾复盘

## P2 - Reliability

### 5. 无容错和重试机制
- 整个 pipeline 线性执行，任一步失败全部失败
- 网络抖动 → Playwright 超时 → 全部重来
- Claude CLI rate limit → 无 backoff 重试
- 数据采集成功但复盘生成失败 → 下次运行重新采集（应能从中间步骤恢复）
- **改进方向**：checkpoint 机制（采集结果已保存到 JSON，复盘生成失败时应复用）

### 6. Claude CLI exit=1 的 workaround
- CLI 2.1.81 在长响应末尾偶发 `signature_delta` JSON 解析错误导致 exit=1
- 当前 workaround：stdout 重定向到文件，exit!=0 但有输出时视为成功
- 应在 CLI 升级后移除此 workaround

## P3 - Performance

### 7. 数据采集可并行化
- zlog、sector、dashboard 三个页面互相独立
- 当前串行采集 ~50 秒，可用多 tab/多 context 并行压缩到 ~25 秒
- 对 cron 不紧急，但工程上更优

## Notes

- TOPACT 历史列（0321, 0314 等）是 BJ 周六日期 = NY 周五收盘的周度快照
- CLAUDE.md 中关于历史列日期的规则描述不够精确（说是 NY 日期，实际是 BJ 日期）
- 复盘生成耗时约 13-20 分钟（Claude opus），timeout 设为 9000 秒（150 分钟）

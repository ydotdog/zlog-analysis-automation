# zlog-analysis-automation

美股每日/每周自动复盘系统。从 Chronoweb 采集市场数据，用 Claude AI 生成结构化复盘报告。

## 工作流程

```
Chronoweb 数据采集 (Playwright)
  ├─ zlog 页面 → Sentiment, ETF, TD, MKT Sum, Trace
  ├─ zlog MS 按钮 → MS Daily 历史表
  ├─ sector 页面 → 25 板块概览 + 个股持仓
  └─ dashboard 页面 → TOPACT 异动表 (WebSocket 终端)
        │
        ▼
Claude CLI Opus + WebSearch/WebFetch
  模型分析数据，自主判断哪些标的需要搜索新闻，
  搜索结果直接融入复盘分析（一步完成）
        │
        ▼
保存到 复盘/ 目录
```

**每周复盘**：周六自动汇总本周所有每日复盘，从更高视角分析叙事生命周期和板块轮动。

## 快速开始

```bash
# 1. 安装依赖
./setup.sh

# 2. 配置凭证
cp .env.example .env
# 编辑 .env，填入 Chronoweb 账号密码

# 3. 运行
python3 main.py              # 自动检测最新交易日
python3 main.py --date 20260324   # 指定北京日期
python3 weekly.py             # 生成本周周复盘
```

## 前置依赖

- Python 3
- [Claude Code CLI](https://github.com/anthropics/claude-code)（需已登录）
- Chromium（由 Playwright 自动安装）

## 命令行参数

### main.py — 每日复盘

| 参数 | 说明 |
|------|------|
| `--date YYYYMMDD` | 指定北京日期，默认自动检测最新交易日 |
| `--install-cron` | 显示 cron 定时任务安装指令 |
| `--debug` | 调试模式，显示详细日志 |

### weekly.py — 每周复盘

| 参数 | 说明 |
|------|------|
| `--date YYYYMMDD` | 指定周六的北京日期，默认自动检测 |
| `--debug` | 调试模式 |

## 定时任务

```bash
python3 main.py --install-cron
```

生成两条 cron 规则：
- **每日复盘**：周二至周六 12:00（北京时间）
- **每周复盘**：周六 12:30（北京时间）

## 项目结构

```
├── main.py           # 每日复盘主流程（采集 → 生成 → 保存）
├── weekly.py         # 每周复盘
├── scraper.py        # Playwright 数据采集 + 解析 + 校验
├── config.py         # 配置：URL、路径、日期逻辑
├── setup.sh          # 一键安装脚本
├── .env.example      # 凭证模板
├── requirements.txt  # Python 依赖
└── TODO.md           # 已知问题和优化方向
```

**运行时生成的目录：**

```
logs/                 # 日志 + scrape_*.json 采集 checkpoint
  ├── daily.log
  ├── weekly.log
  └── scrape_20260324.json
sector/               # Sector 页面 HTML 快照
复盘/                  # 最终复盘报告 (markdown)
  ├── 20260323_NY_周一.md
  ├── 20260324_NY_周二.md
  └── 20260328_三月W4_周复盘.md
```

## 配置项

### .env

```bash
CHRONOWEB_USER=your_email@example.com
CHRONOWEB_PASS=your_password
DATA_DIR=/path/to/output          # 可选，默认 ~/Downloads/zlog_sector_daily
```

### config.py 可调参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CLAUDE_MODEL` | `claude-opus-4-6` | Claude 模型 |
| `CLAUDE_CMD` | `claude` | Claude CLI 路径 |

### CLAUDE.md — 模型行为控制

`DATA_DIR/CLAUDE.md` 作为 system prompt 传给模型，控制复盘的输出结构和风格。其中应包含工具使用指引，告诉模型在分析过程中何时主动使用 WebSearch 搜索新闻（如 VR 极端值、异常涨跌幅、板块集体异动等）。

## 容错机制

- **Checkpoint 复用**：采集数据保存为 `scrape_*.json`，复盘生成失败时下次跳过采集直接复用
- **重试退避**：Claude CLI 调用失败时自动重试（30s → 60s → 120s）
- **数据校验**：采集后自动校验 zlog 关键字段、板块数量、TOPACT 格式，异常记 warning
- **故障截图**：数据为空时自动保存截图（`zlog_empty.png`、`sector_empty.png`、`dashboard_empty.png`）

## 日期逻辑

系统处理北京时间和纽约时间的映射：

- Chronoweb 所有页面 URL（zlog、sector、dashboard）均使用**北京日期**
- Dashboard 页面内容中标注的时间为**纽约时间**
- 转换规则：`北京日期 - 1天 = 纽约交易日`
- 北京时间 12:00 后视为当日数据可用
- 自动跳过周末和美股休市日

## 日志

所有步骤记录到 `logs/daily.log`（和 stdout），包含：
- 每步耗时和数据大小
- Claude CLI 完整调用参数、exit code、输出长度
- 异常短输出时记录完整内容（用于排查 WebSearch 失效等问题）
- 数据质量摘要（每个字段的 OK/SHORT/空 状态）

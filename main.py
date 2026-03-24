#!/usr/bin/env python3
"""
每日自动复盘主流程：
  1. 计算日期
  2. Playwright 采集数据 (sector + MS + dashboard/TOPACT)
  3. Claude CLI (opus) 搜索异动标的新闻
  4. Claude CLI (opus) 生成每日复盘
  5. 保存 markdown 到 复盘/ 目录
"""

import sys
import subprocess
import logging
import argparse
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import config
from scraper import run_scraper

logger = logging.getLogger(__name__)

# 确保日志目录存在
config.LOG_DIR.mkdir(exist_ok=True)


def run_claude_cli(prompt: str, system_prompt: str = None,
                   allowed_tools: str = None, max_budget: float = None,
                   timeout: int = 9000) -> str:
    """
    调用 Claude CLI (-p 模式)，返回输出文本。
    prompt 通过临时文件 stdin 传递，输出重定向到临时文件（避免 capture_output 缓冲问题）。
    即使 CLI exit!=0，只要有输出内容就返回（绕过 signature_delta 解析 bug）。
    """
    cmd = [config.CLAUDE_CMD, "-p", "--model", config.CLAUDE_MODEL]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    if allowed_tools:
        for tool in allowed_tools.split():
            cmd.extend(["--allowedTools", tool])

    if max_budget:
        cmd.extend(["--max-budget-usd", str(max_budget)])

    # 临时文件：prompt 输入 + stdout 输出
    prompt_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    prompt_file.write(prompt)
    prompt_file.close()

    output_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    output_file.close()

    try:
        with open(prompt_file.name, "r", encoding="utf-8") as fin, \
             open(output_file.name, "w", encoding="utf-8") as fout:
            result = subprocess.run(
                cmd,
                stdin=fin,
                stdout=fout,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )

        output = Path(output_file.name).read_text(encoding="utf-8").strip()

        if result.returncode != 0:
            logger.warning(f"Claude CLI exit={result.returncode}, stderr: {result.stderr[:300]}")
            # 即使 exit!=0，如果已有实质输出就返回（绕过 signature_delta bug）
            if output:
                logger.info(f"虽然 exit!=0，但已有 {len(output)} 字符输出，视为成功")
                return output
            logger.error("Claude CLI 失败且无有效输出")
            return ""

        return output

    except subprocess.TimeoutExpired:
        # 超时时也尝试读取已有输出
        output = Path(output_file.name).read_text(encoding="utf-8").strip()
        if output:
            logger.warning(f"Claude CLI 超时但已有 {len(output)} 字符输出，视为成功")
            return output
        logger.error(f"Claude CLI 调用超时 ({timeout}秒)")
        return ""
    finally:
        Path(prompt_file.name).unlink(missing_ok=True)
        Path(output_file.name).unlink(missing_ok=True)


def search_news(tickers: list, ny_date: datetime) -> str:
    """
    用 Claude CLI + WebSearch 搜索异动标的新闻。
    一次性搜索所有标的，返回新闻汇总文本。
    """
    if not tickers:
        return "无异动标的需要搜索新闻。"

    date_str = ny_date.strftime("%Y-%m-%d")
    ticker_str = ", ".join(tickers)

    prompt = f"""请搜索以下美股标的在 {date_str} 前后（纽约时间）的最新新闻。
对于每个标的，查找任何重大事件（财报发布、FDA决定、并购、分析师评级调整、政策变动、行业事件等）。

标的列表：{ticker_str}

要求：
1. 对每个标的分别报告，格式为 "**TICKER**: 新闻摘要"
2. 注意新闻发布时间是否与 {date_str} 的价格变动匹配
3. 如果某个标的找不到明确新闻，写 "无重大新闻，消息面待追踪"
4. 只报告事实，不做投资建议
5. 用中文回复"""

    logger.info(f"搜索新闻: {ticker_str}")
    result = run_claude_cli(
        prompt,
        allowed_tools="WebSearch WebFetch",
        max_budget=2.0,
    )

    if not result:
        return f"新闻搜索未返回结果。标的：{ticker_str}"

    return result


def _trim_sector_details(sector_details: str, max_stocks: int = 5) -> str:
    """每个板块只保留前 N 个标的，减少 prompt 大小。"""
    trimmed = []
    for line in sector_details.split("\n"):
        if "|" not in line:
            trimmed.append(line)
            continue
        parts = line.split("|", 1)
        if len(parts) < 2:
            trimmed.append(line)
            continue
        sector_name = parts[0]
        stocks = parts[1].split(";")
        trimmed.append(f"{sector_name}|{';'.join(stocks[:max_stocks])}")
    return "\n".join(trimmed)


def _trim_topact(topact_text: str) -> str:
    """只保留 TOPACT 第一页（约30条最重要的标的）+ 底部汇总行。"""
    # 找到第一页和第二页的分界（空行分隔）
    pages = topact_text.split("\n\n")
    if pages:
        return pages[0]  # 只返回第一页
    return topact_text[:8000]


def _trim_ms_table(ms_text: str, max_rows: int = 22) -> str:
    """只保留 MS 表的表头 + Ex 行 + 最近 N 个交易日，减少 prompt 大小。"""
    lines = ms_text.split("\n")
    # 保留表头（前6行：标题、边框、列名、边框、Ex行、分隔行）和最近的数据行
    header_lines = []
    data_lines = []
    footer_lines = []
    in_data = False
    for line in lines:
        if "Ex" in line or "DT" in line or "┌" in line or "├" in line:
            header_lines.append(line)
            in_data = True
        elif "└" in line:
            footer_lines.append(line)
        elif in_data and "│" in line:
            data_lines.append(line)
        else:
            header_lines.append(line)

    # 保留 Ex 行 + 最近 max_rows 个数据行
    trimmed = header_lines + data_lines[:max_rows] + footer_lines
    return "\n".join(trimmed)


def generate_daily_review(zlog_text: str, ms_text: str, sector_summary: str,
                          sector_details: str, topact_text: str, news_text: str,
                          bj_date: datetime, ny_date: datetime) -> str:
    """
    用 Claude CLI 生成每日复盘 markdown。
    """
    # 读取 CLAUDE.md 作为 system prompt
    system_prompt = ""
    if config.CLAUDE_MD_PATH.exists():
        system_prompt = config.CLAUDE_MD_PATH.read_text(encoding="utf-8")

    # 读取前一天的复盘作为上下文
    prev_review = ""
    prev_ny = ny_date - timedelta(days=1)
    for _ in range(5):  # 回溯最多5天找到上一个交易日的复盘
        prev_filename = config.format_review_filename(prev_ny)
        prev_path = config.REVIEW_DIR / prev_filename
        if prev_path.exists():
            prev_review = prev_path.read_text(encoding="utf-8")
            break
        prev_ny -= timedelta(days=1)

    ny_display = config.format_ny_date_display(ny_date)
    bj_str = config.format_bj_date_str(bj_date)

    # 构造 prompt
    prompt_parts = [
        f"请根据以下数据生成 {ny_display} 的每日美股复盘报告。",
        f"数据日期：北京时间 {bj_date.month}月{bj_date.day}日 = 纽约时间 {ny_date.month}月{ny_date.day}日",
        "",
        "=" * 60,
        "【一、Sentiment / ETF / TD / MKT Sum / Trace 数据】",
        "（来自 zlog 默认视图）",
        "=" * 60,
        zlog_text if zlog_text else "（zlog 数据未获取到）",
        "",
        "=" * 60,
        "【二、MS Daily 表（市场整体指标历史，最近20个交易日）】",
        "=" * 60,
        _trim_ms_table(ms_text) if ms_text else "（MS 数据未获取到）",
        "",
        "=" * 60,
        "【三、板块概览（按 Hi 排序）】",
        "格式：排名|ID|板块名|Hi|Chg",
        "=" * 60,
        sector_summary if sector_summary else "（板块概览未获取到）",
        "",
        "=" * 60,
        "【四、板块详情（各板块持仓）】",
        "格式：板块名|TICKER,TO,Chg,状态;...",
        "=" * 60,
        _trim_sector_details(sector_details) if sector_details else "（板块详情未获取到）",
        "",
        "=" * 60,
        "【五、TOPACT 异动表（按 TO 排序，前30名）】",
        "=" * 60,
        _trim_topact(topact_text) if topact_text else "（TOPACT 数据未获取到）",
    ]

    if news_text:
        prompt_parts.extend([
            "",
            "=" * 60,
            "【六、异动标的新闻搜索结果】",
            "=" * 60,
            news_text,
        ])

    if prev_review:
        prompt_parts.extend([
            "",
            "=" * 60,
            "【参考：前一交易日复盘（仅供风格和连续性参考，不要续写）】",
            "=" * 60,
            prev_review[:3000],
        ])

    # 末尾重复任务指令，防止模型被数据淹没后偏离任务
    prompt_parts.extend([
        "",
        "=" * 60,
        f"【任务】请根据以上所有数据，生成 {ny_display} 的完整每日美股复盘报告。",
        "从标题 '# 美股复盘 · ...' 开始，严格按照 system prompt 中定义的输出结构生成。",
        "这是一份全新的独立报告，不是对上述参考复盘的补充或续写。",
        "=" * 60,
    ])

    prompt = "\n".join(prompt_parts)

    logger.info(f"生成复盘中... (prompt 长度: {len(prompt)} 字符)")
    result = run_claude_cli(
        prompt,
        system_prompt=system_prompt,
        max_budget=5.0,
    )

    return result


def run_daily(target_bj_date: datetime = None):
    """执行每日复盘完整流程。"""

    # 1. 计算日期
    if target_bj_date is None:
        bj_date = config.get_latest_trading_bj_date()
    else:
        bj_date = target_bj_date

    ny_date = config.bj_date_to_ny_date(bj_date)

    logger.info(f"===== 每日复盘开始 =====")
    logger.info(f"目标日期: BJ {bj_date.date()} → NY {ny_date.date()} ({config.WEEKDAY_CN[ny_date.weekday()]})")

    # 检查是否已有复盘
    review_filename = config.format_review_filename(ny_date)
    review_path = config.REVIEW_DIR / review_filename
    if review_path.exists():
        logger.warning(f"复盘文件已存在: {review_path}")
        logger.warning("如需重新生成，请删除该文件后重试。跳过。")
        return review_path

    # 2. 数据采集
    logger.info("--- 步骤 1/4: 数据采集 ---")
    try:
        data = run_scraper(bj_date, ny_date)
    except Exception as e:
        logger.error(f"数据采集失败: {e}")
        raise

    # 3. 搜索新闻
    logger.info("--- 步骤 2/4: 新闻搜索 ---")
    news_text = search_news(data["anomaly_tickers"], ny_date)
    logger.info(f"新闻搜索完成 ({len(news_text)} 字符)")

    # 4. 生成复盘
    logger.info("--- 步骤 3/4: 生成复盘 ---")
    review = generate_daily_review(
        zlog_text=data["zlog_text"],
        ms_text=data["ms_text"],
        sector_summary=data["sector_summary"],
        sector_details=data["sector_details"],
        topact_text=data["topact_text"],
        news_text=news_text,
        bj_date=bj_date,
        ny_date=ny_date,
    )

    if not review:
        logger.error("复盘生成失败，Claude CLI 未返回内容")
        return None

    # 5. 保存
    logger.info("--- 步骤 4/4: 保存复盘 ---")
    review_path.write_text(review, encoding="utf-8")
    logger.info(f"复盘已保存: {review_path}")
    logger.info(f"===== 每日复盘完成: {review_filename} =====")

    return review_path


def install_cron():
    """安装 cron 定时任务。"""
    script_path = Path(__file__).resolve()
    weekly_path = script_path.parent / "weekly.py"
    python_path = sys.executable
    log_dir = config.LOG_DIR

    # 每日任务：周二到周六中午12点（北京时间）
    daily_cron = (
        f"0 12 * * 2-6 cd {script_path.parent} && "
        f"{python_path} {script_path} "
        f">> {log_dir}/daily_cron.log 2>&1"
    )

    # 周复盘：周六中午12:30（在每日复盘之后）
    weekly_cron = (
        f"30 12 * * 6 cd {script_path.parent} && "
        f"{python_path} {weekly_path} "
        f">> {log_dir}/weekly_cron.log 2>&1"
    )

    print("请将以下内容添加到 crontab (运行 crontab -e)：")
    print()
    print("# 每日美股复盘 (周二-周六 12:00 北京时间)")
    print(daily_cron)
    print()
    print("# 每周复盘 (周六 12:30 北京时间)")
    print(weekly_cron)
    print()
    print("或者运行以下命令自动添加：")
    print(f'(crontab -l 2>/dev/null; echo "{daily_cron}"; echo "{weekly_cron}") | crontab -')


def main():
    parser = argparse.ArgumentParser(description="每日美股自动复盘")
    parser.add_argument("--date", help="指定北京日期 (YYYYMMDD)，默认自动检测最新交易日")
    parser.add_argument("--install-cron", action="store_true", help="显示 cron 安装指令")
    parser.add_argument("--debug", action="store_true", help="调试模式，显示详细日志")
    args = parser.parse_args()

    # 配置日志
    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOG_DIR / "daily.log", encoding="utf-8"),
        ],
    )

    if args.install_cron:
        install_cron()
        return

    target_bj = None
    if args.date:
        target_bj = datetime.strptime(args.date, "%Y%m%d")

    try:
        result = run_daily(target_bj)
        if result:
            print(f"\n复盘完成: {result}")
        else:
            print("\n复盘生成失败", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        logger.exception(f"运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

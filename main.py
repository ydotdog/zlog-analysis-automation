#!/usr/bin/env python3
"""
每日自动复盘主流程：
  1. 计算日期
  2. Playwright 采集数据 (sector + MS + dashboard/TOPACT)
  3. Claude CLI (opus) 生成复盘（模型自主搜索新闻）
  4. 保存 markdown 到 复盘/ 目录
"""

import sys
import subprocess
import logging
import argparse
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import config
from scraper import run_scraper

logger = logging.getLogger(__name__)

# 确保日志目录存在
config.LOG_DIR.mkdir(exist_ok=True)


def run_claude_cli(prompt: str, system_prompt: str = None,
                   allowed_tools: str = None, max_budget: float = None,
                   timeout: int = 9000, caller: str = "",
                   max_retries: int = 1) -> str:
    """
    调用 Claude CLI (-p 模式)，返回输出文本。
    失败时自动重试（指数退避），最多 max_retries 次。
    """
    tag = f"[claude-cli:{caller}]" if caller else "[claude-cli]"

    for attempt in range(1, max_retries + 2):  # attempt 1 = 首次调用
        result = _run_claude_cli_once(
            prompt, system_prompt, allowed_tools, max_budget, timeout, tag
        )
        if result:
            return result

        if attempt <= max_retries:
            backoff = 30 * (2 ** (attempt - 1))  # 30s, 60s, 120s...
            logger.warning(f"{tag} 第{attempt}次重试，等待 {backoff}s...")
            time.sleep(backoff)
        else:
            logger.error(f"{tag} 已用尽 {max_retries} 次重试机会")

    return ""


def _run_claude_cli_once(prompt: str, system_prompt: str, allowed_tools: str,
                         max_budget: float, timeout: int, tag: str) -> str:
    """单次 Claude CLI 调用。返回输出文本，失败返回空字符串。"""
    cmd = [config.CLAUDE_CMD, "-p", "--model", config.CLAUDE_MODEL]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    if allowed_tools:
        for tool in allowed_tools.split():
            cmd.extend(["--allowedTools", tool])

    if max_budget:
        cmd.extend(["--max-budget-usd", str(max_budget)])

    # 记录调用参数（不含 system_prompt 内容，太长）
    cmd_display = [c for c in cmd if c != system_prompt]
    logger.info(f"{tag} 调用开始: {' '.join(cmd_display)}")
    logger.info(f"{tag} prompt={len(prompt)}字符, system_prompt={len(system_prompt) if system_prompt else 0}字符")
    logger.debug(f"{tag} prompt前200字符: {prompt[:200]}")

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

    t0 = time.monotonic()

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

        elapsed = time.monotonic() - t0
        output = Path(output_file.name).read_text(encoding="utf-8").strip()

        logger.info(
            f"{tag} 调用完成: exit={result.returncode}, "
            f"output={len(output)}字符, elapsed={elapsed:.1f}s"
        )

        if result.stderr:
            logger.debug(f"{tag} stderr: {result.stderr[:500]}")

        if result.returncode != 0:
            logger.warning(
                f"{tag} 非零退出码 exit={result.returncode}, "
                f"stderr: {result.stderr[:500]}"
            )
            if output:
                logger.info(f"{tag} 虽然 exit!=0，但已有 {len(output)} 字符输出，视为成功")
                if len(output) < 200:
                    logger.warning(f"{tag} 输出较短，完整内容: [{output}]")
                return output
            logger.error(f"{tag} 失败且无有效输出")
            return ""

        # exit=0 但输出异常短时，记录完整内容供排查
        if len(output) < 200:
            logger.warning(
                f"{tag} 输出仅 {len(output)} 字符（可能异常），"
                f"完整内容: [{output}]"
            )

        return output

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        output = Path(output_file.name).read_text(encoding="utf-8").strip()
        logger.error(
            f"{tag} 超时 ({elapsed:.0f}s/{timeout}s), "
            f"已有输出={len(output)}字符"
        )
        if output:
            logger.warning(f"{tag} 超时但已有 {len(output)} 字符输出，视为成功")
            if len(output) < 200:
                logger.warning(f"{tag} 超时输出完整内容: [{output}]")
            return output
        return ""
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(f"{tag} 异常: {type(e).__name__}: {e}, elapsed={elapsed:.1f}s")
        return ""
    finally:
        Path(prompt_file.name).unlink(missing_ok=True)
        Path(output_file.name).unlink(missing_ok=True)


def generate_daily_review(zlog_text: str, ms_text: str, sector_summary: str,
                          sector_details: str, topact_text: str,
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
        "【二、MS Daily 表（市场整体指标历史）】",
        "=" * 60,
        ms_text if ms_text else "（MS 数据未获取到）",
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
        sector_details if sector_details else "（板块详情未获取到）",
        "",
        "=" * 60,
        "【五、TOPACT 异动表（按 TO 排序）】",
        "=" * 60,
        topact_text if topact_text else "（TOPACT 数据未获取到）",
    ]

    if prev_review:
        prompt_parts.extend([
            "",
            "=" * 60,
            "【参考：前一交易日复盘（仅供风格和连续性参考，不要续写）】",
            "=" * 60,
            prev_review,
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
        allowed_tools="WebSearch WebFetch",
        max_budget=8.0,
        caller="generate_review",
    )

    if not result:
        logger.error("复盘生成返回空结果")
    elif len(result) < 500:
        logger.warning(
            f"复盘结果异常短: {len(result)}字符 (期望>=2000)。"
            f"完整内容: [{result}]"
        )

    return result


def run_daily(target_bj_date: datetime = None, suffix: str = ""):
    """执行每日复盘完整流程。suffix 用于文件名后缀（如 '—new'）。"""

    # 1. 计算日期
    if target_bj_date is None:
        bj_date = config.get_latest_trading_bj_date()
    else:
        bj_date = target_bj_date

    ny_date = config.bj_date_to_ny_date(bj_date)

    logger.info(f"===== 每日复盘开始 =====")
    logger.info(f"目标日期: BJ {bj_date.date()} → NY {ny_date.date()} ({config.WEEKDAY_CN[ny_date.weekday()]})")

    # 检查是否已有复盘
    base_filename = config.format_review_filename(ny_date)
    if suffix:
        review_filename = base_filename.replace(".md", f"{suffix}.md")
    else:
        review_filename = base_filename
    review_path = config.REVIEW_DIR / review_filename
    if review_path.exists():
        logger.warning(f"复盘文件已存在: {review_path}")
        logger.warning("如需重新生成，请删除该文件后重试。跳过。")
        return review_path

    # 2. 数据采集（有 checkpoint 时复用）
    logger.info("--- 步骤 1/3: 数据采集 ---")
    bj_str = config.format_bj_date_str(bj_date)
    checkpoint_path = config.LOG_DIR / f"scrape_{bj_str}.json"
    t_scrape = time.monotonic()

    if checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            # 验证 checkpoint 包含必要字段且非空
            required = ["zlog_text", "ms_text", "sector_details", "topact_text"]
            if all(data.get(f) for f in required):
                logger.info(f"复用已有采集数据: {checkpoint_path}")
                t_scrape = 0
            else:
                missing = [f for f in required if not data.get(f)]
                logger.warning(f"Checkpoint 数据不完整 (缺: {missing})，重新采集")
                data = run_scraper(bj_date, ny_date)
                t_scrape = time.monotonic() - t_scrape
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Checkpoint 文件损坏 ({e})，重新采集")
            data = run_scraper(bj_date, ny_date)
            t_scrape = time.monotonic() - t_scrape
    else:
        try:
            data = run_scraper(bj_date, ny_date)
        except Exception as e:
            logger.error(f"数据采集失败: {e}")
            raise
        t_scrape = time.monotonic() - t_scrape

    # 数据质量摘要
    data_fields = {
        "zlog_text": ("Sentiment/ETF/TD", 100),
        "ms_text": ("MS表", 200),
        "sector_summary": ("板块概览", 50),
        "sector_details": ("板块详情", 100),
        "topact_text": ("TOPACT", 200),
    }
    logger.info(f"数据采集完成 ({t_scrape:.1f}s)，质量摘要:")
    for field, (label, min_len) in data_fields.items():
        actual = len(data.get(field, ""))
        status = "OK" if actual >= min_len else "SHORT"
        logger.info(f"  {label}: {actual}字符 [{status}]")
        if actual < min_len and actual > 0:
            logger.warning(f"  {label} 内容偏短，前200字符: [{data[field][:200]}]")
        elif actual == 0:
            logger.warning(f"  {label} 为空！")
    # 3. 生成复盘（模型自主搜索新闻）
    logger.info("--- 步骤 2/3: 生成复盘 ---")
    t_review = time.monotonic()
    review = generate_daily_review(
        zlog_text=data["zlog_text"],
        ms_text=data["ms_text"],
        sector_summary=data["sector_summary"],
        sector_details=data["sector_details"],
        topact_text=data["topact_text"],
        bj_date=bj_date,
        ny_date=ny_date,
    )
    t_review = time.monotonic() - t_review

    if not review:
        logger.error("复盘生成失败，Claude CLI 未返回内容")
        return None

    # 4. 保存
    logger.info("--- 步骤 3/3: 保存复盘 ---")
    review_path.write_text(review, encoding="utf-8")
    logger.info(f"复盘已保存: {review_path} ({len(review)}字符)")
    logger.info(
        f"===== 每日复盘完成: {review_filename} "
        f"(采集{t_scrape:.0f}s + 复盘{t_review:.0f}s) ====="
    )

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
    parser.add_argument("--suffix", default="", help="输出文件名后缀（如 '—new'）")
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
        result = run_daily(target_bj, suffix=args.suffix)
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

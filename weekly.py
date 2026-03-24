#!/usr/bin/env python3
"""
每周复盘生成：
  1. 收集本周所有每日复盘 (NY 周一~周五)
  2. 汇总数据
  3. 调用 Claude CLI 生成周复盘
  4. 保存到 复盘/ 目录
"""

import sys
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import config
from main import run_claude_cli, run_daily

logger = logging.getLogger(__name__)


def get_month_week_label(ny_dates: list) -> str:
    """
    生成周复盘标签，如 "三月W3"。
    基于本周多数天数所在的月份。
    """
    months_cn = {
        1: "一月", 2: "二月", 3: "三月", 4: "四月",
        5: "五月", 6: "六月", 7: "七月", 8: "八月",
        9: "九月", 10: "十月", 11: "十一月", 12: "十二月",
    }
    if not ny_dates:
        return "未知"

    # 用周五的月份
    last_date = ny_dates[-1]
    month = last_date.month
    month_cn = months_cn.get(month, str(month))

    # 计算是当月第几周
    first_day_of_month = last_date.replace(day=1)
    week_num = (last_date.day - 1) // 7 + 1

    return f"{month_cn}W{week_num}"


def generate_weekly_review(saturday_bj: datetime = None):
    """生成周复盘。"""

    if saturday_bj is None:
        now = config.get_beijing_now()
        saturday_bj = now
        # 如果不是周六，找到最近的周六
        while saturday_bj.weekday() != 5:  # 5 = Saturday
            saturday_bj -= timedelta(days=1)

    # 获取本周所有交易日
    week_dates = config.get_week_trading_dates(saturday_bj)
    if not week_dates:
        logger.error("本周没有交易日")
        return None

    ny_dates = [ny for _, ny in week_dates]
    logger.info(f"本周交易日: {[d.strftime('%m/%d %a') for d in ny_dates]}")

    # 确保所有每日复盘都已生成
    daily_reviews = []
    for bj_d, ny_d in week_dates:
        filename = config.format_review_filename(ny_d)
        filepath = config.REVIEW_DIR / filename
        if not filepath.exists():
            logger.warning(f"每日复盘不存在: {filename}，尝试生成...")
            try:
                run_daily(bj_d)
            except Exception as e:
                logger.error(f"生成 {filename} 失败: {e}")
                continue

        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            daily_reviews.append({
                "ny_date": ny_d,
                "filename": filename,
                "content": content,
            })

    if not daily_reviews:
        logger.error("没有可用的每日复盘，无法生成周复盘")
        return None

    logger.info(f"收集到 {len(daily_reviews)} 篇每日复盘")

    # 收集本周的 MS 数据用于周度汇总表
    ms_texts = []
    for bj_d, ny_d in week_dates:
        bj_str = config.format_bj_date_str(bj_d)
        ms_path = config.MS_DIR / f"MS-{bj_str[4:8]}.html"
        if ms_path.exists():
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(ms_path.read_text(encoding="utf-8"), "html.parser")
            output_div = soup.find("div", class_="output")
            if output_div:
                ms_texts.append(output_div.get_text()[:1000])

    # 构造周复盘 prompt
    week_label = get_month_week_label(ny_dates)
    start_date = ny_dates[0]
    end_date = ny_dates[-1]
    start_display = f"{start_date.month}月{start_date.day}日"
    end_display = f"{end_date.month}月{end_date.day}日"
    start_wd = config.WEEKDAY_CN[start_date.weekday()]
    end_wd = config.WEEKDAY_CN[end_date.weekday()]

    prompt_parts = [
        f"请根据以下本周每日复盘，生成 {week_label} 周复盘报告。",
        f"时间范围：{start_date.year}年{start_display}（{start_wd}）至 {end_display}（{end_wd}），纽约时间。",
        "",
        "周复盘要求：",
        "1. 开头制作一周数据总览表（每日 Chg/Gap/Shw/VR/Yes/Lit）",
        "2. 分析本周主要叙事/主题的形成与消亡（哪些叙事被市场验证，哪些被推翻）",
        "3. 信号 vs 噪声判断（哪些是持续趋势，哪些是单日噪声）",
        "4. 板块轮动总结（哪些板块全周强势/弱势/反转）",
        "5. 提出进入下周的关键观察问题",
        "6. 保持数据驱动、拒绝猜测的核心原则",
        "",
    ]

    if ms_texts:
        prompt_parts.append("=" * 60)
        prompt_parts.append("【MS 数据参考（最新）】")
        prompt_parts.append("=" * 60)
        prompt_parts.append(ms_texts[-1] if ms_texts else "")
        prompt_parts.append("")

    for review in daily_reviews:
        ny_d = review["ny_date"]
        wd = config.WEEKDAY_CN[ny_d.weekday()]
        prompt_parts.append("=" * 60)
        prompt_parts.append(f"【{ny_d.month}/{ny_d.day} {wd} 每日复盘】")
        prompt_parts.append("=" * 60)
        prompt_parts.append(review["content"])
        prompt_parts.append("")

    prompt = "\n".join(prompt_parts)

    # 读取 system prompt
    system_prompt = ""
    if config.CLAUDE_MD_PATH.exists():
        base_prompt = config.CLAUDE_MD_PATH.read_text(encoding="utf-8")
        system_prompt = base_prompt + "\n\n## 周复盘补充说明\n你现在需要生成的是【周复盘】而非每日复盘。周复盘应该从更高的视角审视一整周的市场行为，重点分析叙事的生命周期、信号与噪声的区分、以及对下周的前瞻。"

    logger.info(f"生成周复盘中... (prompt 长度: {len(prompt)} 字符)")
    review_text = run_claude_cli(
        prompt,
        system_prompt=system_prompt,
        max_budget=8.0,
    )

    if not review_text:
        logger.error("周复盘生成失败")
        return None

    # 保存
    filename = f"{end_date.strftime('%Y%m%d')}_{week_label}_周复盘.md"
    filepath = config.REVIEW_DIR / filename
    filepath.write_text(review_text, encoding="utf-8")
    logger.info(f"周复盘已保存: {filepath}")

    return filepath


def main():
    parser = argparse.ArgumentParser(description="每周美股复盘")
    parser.add_argument("--date", help="指定周六的北京日期 (YYYYMMDD)，默认自动检测")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    config.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOG_DIR / "weekly.log", encoding="utf-8"),
        ],
    )

    saturday_bj = None
    if args.date:
        saturday_bj = datetime.strptime(args.date, "%Y%m%d")

    try:
        result = generate_weekly_review(saturday_bj)
        if result:
            print(f"\n周复盘完成: {result}")
        else:
            print("\n周复盘生成失败", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        logger.exception(f"运行出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

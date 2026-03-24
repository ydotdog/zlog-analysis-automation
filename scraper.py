"""
Playwright 数据采集：登录 chronoweb，抓取 zlog/sector/dashboard 三个页面数据。

数据源架构：
- zlog 页面 (div.output): Sentiment(Yes/Lit), ETF Index, TD, MKT Sum, Trace data
- zlog 页面 (需点击 MS 按钮): MS daily 表（Chg/Gap/Shw/VR 历史表）
- sector 页面 (script 标签): 板块概览 + 板块个股详情
- dashboard 页面 (WebSocket 终端): TOPACT 表（需点击 G+4，用 pyte 解析 ANSI）
"""

import re
import json
import logging
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page
import pyte

import config

logger = logging.getLogger(__name__)


def login(page: Page) -> bool:
    """登录 chronoweb。"""
    logger.info("正在登录 chronoweb...")
    page.goto(config.BASE_URL, wait_until="networkidle", timeout=30000)

    if "/login" not in page.url:
        logger.info("已处于登录状态")
        return True

    page.fill('input[name="username"]', config.CHRONOWEB_USER)
    page.fill('input[name="password"]', config.CHRONOWEB_PASS)
    page.click('button:text("Login")')
    page.wait_for_load_state("networkidle", timeout=15000)

    if "/login" in page.url:
        page.screenshot(path=str(config.LOG_DIR / "login_failed.png"))
        logger.error("登录失败")
        return False

    logger.info("登录成功")
    return True


def fetch_zlog_page(page: Page, bj_date: datetime) -> dict:
    """
    抓取 zlog 页面。
    默认视图：Sentiment, ETF Index, TD, MKT Sum, Trace data
    点击 MS 按钮后：MS daily 表
    """
    url = config.ZLOG_URL_TPL.format(
        year=bj_date.year, month=bj_date.month, day=bj_date.day
    )
    logger.info(f"抓取 zlog 页面: {url}")
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    # 提取默认视图文本（Sentiment, ETF, TD, MKT Sum, Trace）
    soup = BeautifulSoup(page.content(), "html.parser")
    output_div = soup.find("div", class_="output")
    zlog_text = output_div.get_text().strip() if output_div else ""
    if not zlog_text:
        page.screenshot(path=str(config.LOG_DIR / "zlog_empty.png"))
        logger.error("zlog 默认视图为空，已截图 zlog_empty.png")
    else:
        logger.info(f"zlog 默认视图: {len(zlog_text)} 字符")

    # 点击 MS 按钮获取 MS daily 表
    ms_text = ""
    ms_btn = page.locator("button:text-is('MS')")
    if ms_btn.count() > 0:
        ms_btn.first.click()
        page.wait_for_timeout(2000)
        page.wait_for_load_state("networkidle", timeout=10000)

        soup2 = BeautifulSoup(page.content(), "html.parser")
        output_div2 = soup2.find("div", class_="output")
        ms_text = output_div2.get_text().strip() if output_div2 else ""
        logger.info(f"MS daily 表: {len(ms_text)} 字符")

        # 保存 MS HTML
        bj_str = config.format_bj_date_str(bj_date)
        html_path = config.MS_DIR / f"MS-{bj_str[4:8]}.html"
        html_path.write_text(page.content(), encoding="utf-8")
    else:
        logger.warning("未找到 MS 按钮，使用默认视图")
        ms_text = zlog_text

    return {
        "zlog_text": zlog_text,  # Sentiment/ETF/TD/MKT Sum/Trace
        "ms_text": ms_text,       # MS daily 表
    }


def fetch_sector_page(page: Page, bj_date: datetime) -> dict:
    """
    抓取 sector 页面。
    页面是 JS SPA，数据从 DOM 元素提取：
    - .sector-item: 板块概览 (ID, 名称, Chg, VR, Hi, Di)
    - .symbol-item: 当前选中板块的个股 (需逐个板块点击提取)
    """
    url = config.SECTOR_URL_TPL.format(
        year=bj_date.year, month=bj_date.month, day=bj_date.day
    )
    logger.info(f"抓取 sector 页面: {url}")
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)

    # 保存 HTML
    bj_str = config.format_bj_date_str(bj_date)
    html_path = config.SECTOR_DIR / f"{bj_str}.html"
    html_path.write_text(page.content(), encoding="utf-8")

    # 提取板块概览
    sector_lines = []
    sector_items = page.locator(".sector-item").all()
    for item in sector_items:
        text = item.inner_text().strip()
        # 格式: "07.\n行业服务\n-0.01\n29.2\n17.7\n-67.2"
        parts = [p.strip() for p in text.split("\n") if p.strip()]
        if len(parts) >= 5:
            sector_lines.append("|".join(parts))

    sector_summary = "\n".join(sector_lines)

    # 提取每个板块的个股详情（点击每个板块，读取 symbol-list）
    sector_details_parts = []
    for i, item in enumerate(sector_items):
        try:
            item.click()
            page.wait_for_timeout(500)

            # 获取板块名
            text = item.inner_text().strip()
            parts = [p.strip() for p in text.split("\n") if p.strip()]
            sector_name = parts[1] if len(parts) >= 2 else f"sector_{i}"

            # 获取当前显示的股票
            symbols = page.locator(".symbol-item").all()
            stock_entries = []
            for sym in symbols:
                name_el = sym.locator(".symbol-name")
                ticker = name_el.inner_text().strip() if name_el.count() else ""
                values = sym.locator(".symbol-value").all()
                vals = []
                for v in values:
                    vals.append(v.inner_text().strip().replace("\n", "/"))
                if ticker and vals:
                    stock_entries.append(f"{ticker},{','.join(vals)}")

            if stock_entries:
                sector_details_parts.append(
                    f"{sector_name}|{';'.join(stock_entries)}"
                )
        except Exception as e:
            logger.warning(f"提取板块 {i} 个股失败: {e}")

    sector_details = "\n".join(sector_details_parts)
    logger.info(
        f"Sector 概览: {len(sector_items)} 板块, "
        f"详情: {len(sector_details_parts)} 板块个股"
    )
    if len(sector_items) == 0:
        page.screenshot(path=str(config.LOG_DIR / "sector_empty.png"))
        logger.error("未找到任何板块，已截图 sector_empty.png")
    elif len(sector_details_parts) < len(sector_items) * 0.5:
        logger.warning(
            f"板块详情提取不完整: 只有 {len(sector_details_parts)}/{len(sector_items)} 个板块有个股数据"
        )
    return {
        "sector_summary": sector_summary,
        "sector_details": sector_details,
    }


def fetch_dashboard_topact(page: Page, bj_date: datetime, max_pages: int = 3) -> dict:
    """
    抓取 dashboard TOPACT 数据。
    Dashboard 是 WebSocket 终端应用，通过 pyte 解析 ANSI 终端输出。
    注意：dashboard URL 使用北京日期（与 zlog/sector 一致）。

    流程: 导航 → 收集 WS 消息 → 点击 G → 点击 4 → 用 pyte 重建屏幕 → 翻页
    """
    url = config.DASHBOARD_URL_TPL.format(
        year=bj_date.year, month=bj_date.month, day=bj_date.day
    )
    logger.info(f"抓取 dashboard 页面: {url}")

    # 收集 WebSocket 消息
    ws_messages = []
    def on_ws(ws):
        ws.on("framereceived", lambda payload: ws_messages.append(payload))
    page.on("websocket", on_ws)

    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(5000)

    # 点击 G (收盘数据) 和 4 (TOPACT 视图)
    page.locator("button:text-is('G')").click()
    page.wait_for_timeout(5000)
    page.locator("button:text-is('4')").click()
    page.wait_for_timeout(5000)

    # 用 pyte 重建第一页
    all_pages_text = []
    logger.info(f"Dashboard WS 消息数: {len(ws_messages)}")
    screen_text = _rebuild_screen(ws_messages)
    all_pages_text.append(screen_text)
    if not screen_text:
        page.screenshot(path=str(config.LOG_DIR / "dashboard_empty.png"))
        logger.error("TOPACT 第1页为空，已截图 dashboard_empty.png")
    else:
        logger.info(f"TOPACT 第1页: {len(screen_text)} 字符")

    # 翻页获取更多数据
    for pg in range(2, max_pages + 1):
        prev_count = len(ws_messages)
        # 点击第一个 ">" 按钮（页面导航）
        next_btns = page.locator("button:text-is('>')").all()
        if next_btns:
            next_btns[0].click()  # 第一个 ">" 是页面翻页
            page.wait_for_timeout(4000)
            screen_text = _rebuild_screen(ws_messages)
            # 检查是否有新内容（避免重复）
            if screen_text and screen_text != all_pages_text[-1]:
                all_pages_text.append(screen_text)
                logger.info(f"TOPACT 第{pg}页: {len(screen_text)} 字符")
            else:
                break

    # 保存截图
    page.screenshot(path=str(config.LOG_DIR / "dashboard_final.png"), full_page=True)

    combined_text = "\n\n".join(all_pages_text)
    return {"topact_text": combined_text}


def _rebuild_screen(ws_messages: list) -> str:
    """从 WebSocket 消息重建终端屏幕文本。"""
    screen = pyte.Screen(205, 56)
    stream = pyte.Stream(screen)

    for msg in ws_messages:
        if isinstance(msg, bytes) and msg[:1] == b'0':
            try:
                text = msg[1:].decode("utf-8", errors="replace")
                stream.feed(text)
            except Exception:
                pass

    lines = []
    for i in range(screen.lines):
        row = screen.buffer[i]
        line = ""
        for j in range(screen.columns):
            char = row[j]
            line += char.data if char.data else " "
        lines.append(line.rstrip())

    result = "\n".join(lines)
    return re.sub(r'\n{3,}', '\n\n', result).strip()


def run_scraper(bj_date: datetime, ny_date: datetime) -> dict:
    """执行完整的数据采集流程。"""
    config.LOG_DIR.mkdir(exist_ok=True)
    config.SECTOR_DIR.mkdir(exist_ok=True)
    config.REVIEW_DIR.mkdir(exist_ok=True)

    result = {
        "bj_date": bj_date.strftime("%Y-%m-%d"),
        "ny_date": ny_date.strftime("%Y-%m-%d"),
        "zlog_text": "",
        "ms_text": "",
        "sector_summary": "",
        "sector_details": "",
        "topact_text": "",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = context.new_page()

        try:
            if not login(page):
                raise RuntimeError("登录失败")

            # zlog: Sentiment/ETF/TD/MKT Sum + MS 表
            zlog_data = fetch_zlog_page(page, bj_date)
            result["zlog_text"] = zlog_data["zlog_text"]
            result["ms_text"] = zlog_data["ms_text"]

            # sector: 板块概览 + 个股详情
            sector_data = fetch_sector_page(page, bj_date)
            result["sector_summary"] = sector_data["sector_summary"]
            result["sector_details"] = sector_data["sector_details"]

            # dashboard: TOPACT (WebSocket 终端)
            dashboard_data = fetch_dashboard_topact(page, bj_date, max_pages=3)
            result["topact_text"] = dashboard_data["topact_text"]

        finally:
            browser.close()

    # 数据校验
    validate_scrape_data(result)

    # 保存采集结果
    summary_path = config.LOG_DIR / f"scrape_{config.format_bj_date_str(bj_date)}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    return result


def validate_scrape_data(data: dict):
    """
    校验采集数据的完整性和格式。
    校验失败只记 warning（不中断流程），便于事后排查数据质量问题。
    """
    issues = []

    # zlog: 应包含 Sentiment 相关字段
    zlog = data.get("zlog_text", "")
    if not zlog:
        issues.append("zlog_text 为空")
    else:
        expected_keywords = ["Sentiment", "Yes", "Lit"]
        missing = [k for k in expected_keywords if k not in zlog]
        if missing:
            issues.append(f"zlog_text 缺少关键字段: {missing} (前200字符: [{zlog[:200]}])")

    # MS 表: 应包含表格边框字符
    ms = data.get("ms_text", "")
    if not ms:
        issues.append("ms_text 为空")
    elif "│" not in ms and "┌" not in ms:
        issues.append(f"ms_text 不含表格字符，可能非表格数据 (前200字符: [{ms[:200]}])")

    # sector: 应有 20+ 个板块
    summary = data.get("sector_summary", "")
    if not summary:
        issues.append("sector_summary 为空")
    else:
        sector_count = len([l for l in summary.split("\n") if l.strip()])
        if sector_count < 20:
            issues.append(f"sector_summary 仅 {sector_count} 个板块 (期望>=20)")

    details = data.get("sector_details", "")
    if not details:
        issues.append("sector_details 为空")
    else:
        detail_count = len([l for l in details.split("\n") if "|" in l])
        if detail_count < 20:
            issues.append(f"sector_details 仅 {detail_count} 个板块有个股 (期望>=20)")

    # TOPACT: 应包含 │ 分隔的表格行
    topact = data.get("topact_text", "")
    if not topact:
        issues.append("topact_text 为空")
    else:
        data_rows = [l for l in topact.split("\n") if "│" in l and l.strip()]
        if len(data_rows) < 10:
            issues.append(f"topact_text 仅 {len(data_rows)} 行数据 (期望>=10)")
        # 检查列头是否包含预期字段
        header_found = any("VR" in l for l in topact.split("\n")[:5])
        if not header_found:
            issues.append("topact_text 前5行未发现 VR 列头，表格格式可能变更")

    if issues:
        logger.warning(f"数据校验发现 {len(issues)} 个问题:")
        for issue in issues:
            logger.warning(f"  - {issue}")
    else:
        logger.info("数据校验通过")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bj_date = config.get_latest_trading_bj_date()
    ny_date = config.bj_date_to_ny_date(bj_date)
    logger.info(f"目标: BJ={bj_date.date()}, NY={ny_date.date()}")
    data = run_scraper(bj_date, ny_date)
    for k, v in data.items():
        if isinstance(v, str):
            print(f"{k}: {len(v)} chars")
        else:
            print(f"{k}: {v}")

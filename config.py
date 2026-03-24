"""配置文件：URL、路径、日期逻辑、异动阈值"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
load_dotenv(Path(__file__).parent / ".env")

# === 凭证 ===
CHRONOWEB_USER = os.getenv("CHRONOWEB_USER", "")
CHRONOWEB_PASS = os.getenv("CHRONOWEB_PASS", "")

# === 路径 ===
DATA_DIR = Path(os.getenv("DATA_DIR", "/Users/kyleqi/Downloads/zlog_sector_daily"))
SECTOR_DIR = DATA_DIR / "sector"
MS_DIR = DATA_DIR  # MS-MMDD.html 直接在根目录
REVIEW_DIR = DATA_DIR / "复盘"
AUTOMATION_DIR = Path(__file__).parent
CLAUDE_MD_PATH = DATA_DIR / "CLAUDE.md"
LOG_DIR = AUTOMATION_DIR / "logs"

# === URL ===
BASE_URL = "https://chronoweb.duckdns.org"
LOGIN_URL = f"{BASE_URL}/login"
# sector 和 zlog 页面使用北京日期
SECTOR_URL_TPL = f"{BASE_URL}/sector?year={{year}}&month={{month:02d}}&day={{day:02d}}"
ZLOG_URL_TPL = f"{BASE_URL}/zlog?year={{year}}&month={{month:02d}}&day={{day:02d}}"
# dashboard 也使用北京日期（与 zlog/sector 一致）
DASHBOARD_URL_TPL = (
    f"{BASE_URL}/dashboard"
    "?arg=-p&arg=-y&arg={year}&arg=-m&arg={month:02d}&arg=-d&arg={day:02d}"
)

# === Claude CLI 配置 ===
CLAUDE_MODEL = "claude-opus-4-6"
CLAUDE_CMD = "claude"

# === 美股休市日 (2026) ===
# 格式：纽约时间日期
US_HOLIDAYS_2026 = {
    datetime(2026, 1, 1),   # 元旦
    datetime(2026, 1, 19),  # MLK Day
    datetime(2026, 2, 16),  # 总统日
    datetime(2026, 4, 3),   # 耶稣受难日
    datetime(2026, 5, 25),  # 阵亡将士纪念日
    datetime(2026, 7, 3),   # 独立日（观察日）
    datetime(2026, 9, 7),   # 劳动节
    datetime(2026, 11, 26), # 感恩节
    datetime(2026, 12, 25), # 圣诞节
}

# 星期中文映射
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def get_beijing_now() -> datetime:
    """获取当前北京时间"""
    utc_now = datetime.now(timezone.utc)
    bj_tz = timezone(timedelta(hours=8))
    return utc_now.astimezone(bj_tz)


def bj_date_to_ny_date(bj_date: datetime) -> datetime:
    """北京日期 → 纽约交易日期（BJ - 1天）"""
    return (bj_date - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def ny_date_to_bj_date(ny_date: datetime) -> datetime:
    """纽约交易日期 → 北京日期（NY + 1天）"""
    return (ny_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def is_us_trading_day(ny_date: datetime) -> bool:
    """判断某个纽约日期是否为交易日"""
    d = ny_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if d.weekday() >= 5:  # 周六=5, 周日=6
        return False
    if d.replace(tzinfo=None) in US_HOLIDAYS_2026:
        return False
    return True


def get_latest_trading_bj_date(as_of_bj: datetime = None) -> datetime:
    """
    获取最新的可用数据的北京日期。

    规则：在北京时间中午12点后，当天的数据应该可用。
    BJ日期对应 NY = BJ-1 的交易日。
    如果 NY 日期不是交易日，往前找最近的交易日。
    """
    if as_of_bj is None:
        as_of_bj = get_beijing_now()

    # 如果还没到中午，用昨天的北京日期
    if as_of_bj.hour < 12:
        candidate_bj = as_of_bj - timedelta(days=1)
    else:
        candidate_bj = as_of_bj

    # 对应的 NY 日期
    ny_date = bj_date_to_ny_date(candidate_bj)

    # 如果不是交易日，往前回溯
    for _ in range(7):
        if is_us_trading_day(ny_date):
            return ny_date_to_bj_date(ny_date)
        ny_date -= timedelta(days=1)

    raise ValueError(f"无法找到最近的交易日（从 {candidate_bj.date()} 开始回溯）")


def get_week_trading_dates(saturday_bj: datetime) -> list:
    """
    给定一个周六（北京时间），返回本周所有交易日的 (bj_date, ny_date) 列表。
    一周 = NY 周一~周五。
    """
    # 周六 BJ = NY 周五，往前推到 NY 周一
    ny_friday = bj_date_to_ny_date(saturday_bj)
    # 确保是周五
    while ny_friday.weekday() != 4:
        ny_friday -= timedelta(days=1)

    dates = []
    ny_monday = ny_friday - timedelta(days=4)
    for i in range(5):
        ny_d = ny_monday + timedelta(days=i)
        if is_us_trading_day(ny_d):
            dates.append((ny_date_to_bj_date(ny_d), ny_d))
    return dates


def format_bj_date_str(bj_date: datetime) -> str:
    """北京日期 → 文件名格式 '20260320'"""
    return bj_date.strftime("%Y%m%d")


def format_ny_date_display(ny_date: datetime) -> str:
    """纽约日期 → 显示格式 '2026年3月19日（周四，纽约时间）'"""
    wd = WEEKDAY_CN[ny_date.weekday()]
    return f"{ny_date.year}年{ny_date.month}月{ny_date.day}日（{wd}，纽约时间）"


def format_review_filename(ny_date: datetime) -> str:
    """
    复盘文件名格式: 20260319_NY_周四.md
    注意：这里用纽约日期 + 纽约星期
    """
    wd = WEEKDAY_CN[ny_date.weekday()]
    return ny_date.strftime("%Y%m%d") + f"_NY_{wd}.md"

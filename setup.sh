#!/bin/bash
set -e

echo "=== 安装自动化复盘系统依赖 ==="

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 未安装"
    exit 1
fi
echo "✓ Python3: $(python3 --version)"

# 检查 Claude CLI
if ! command -v claude &> /dev/null; then
    echo "ERROR: claude CLI 未安装，请先安装 Claude Code"
    exit 1
fi
echo "✓ Claude CLI: $(claude --version)"

# 安装 Python 依赖
echo ""
echo "--- 安装 Python 包 ---"
pip3 install --user playwright python-dotenv beautifulsoup4

# 安装 Playwright 浏览器
echo ""
echo "--- 安装 Playwright Chromium 浏览器 ---"
python3 -m playwright install chromium

# 检查 .env
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo ""
    echo "WARNING: .env 文件不存在，请从 .env.example 复制并填入凭证:"
    echo "  cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo "  然后编辑 .env 填入你的账号密码"
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "使用方法:"
echo "  手动运行每日复盘: python3 $SCRIPT_DIR/main.py"
echo "  手动运行周复盘:   python3 $SCRIPT_DIR/weekly.py"
echo "  设置定时任务:     python3 $SCRIPT_DIR/main.py --install-cron"

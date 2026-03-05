#!/bin/bash
# S&P 500 Dashboard - One-Time Setup
# Installs dependencies only. Run update_sp500_dashboard.py manually when needed.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== S&P 500 Dashboard Setup ==="
echo ""

# 1. Install dependencies
echo "[1/2] Installing Python dependencies..."
pip3 install yfinance openpyxl pandas -q 2>/dev/null || pip install yfinance openpyxl pandas -q
echo "  Done."
echo ""

# 2. Remove old cron entries if any
crontab -l 2>/dev/null | grep -v "update_sp500_dashboard" | crontab - 2>/dev/null
echo "[2/2] Removed any old automatic schedules."
echo ""

echo "=== Setup Complete ==="
echo ""
echo "To update the dashboard, run:"
echo "  python3 $SCRIPT_DIR/update_sp500_dashboard.py"
echo ""
echo "Or double-click 'Update Dashboard' shortcut (Mac/Windows) in this folder."

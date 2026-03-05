#!/bin/bash
# macOS: Double-click this file to update the S&P 500 Dashboard
cd "$(dirname "$0")"
echo "Updating S&P 500 Dashboard..."
echo "This will take ~10-15 minutes for 503 companies."
echo ""
python3 update_sp500_dashboard.py
echo ""
echo "Done! You can close this window."
read -p "Press Enter to exit..."

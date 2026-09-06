#!/usr/bin/env bash
# PPWR Compliance Desk launcher for macOS / Linux
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    python3 app.py
elif command -v python >/dev/null 2>&1; then
    python app.py
else
    echo ""
    echo "============================================================"
    echo " Python 3 was not found on this computer."
    echo ""
    echo " Install it once (macOS: 'brew install python3', or from"
    echo " https://www.python.org/downloads/), then run this script"
    echo " again: ./start.sh"
    echo "============================================================"
    echo ""
    read -p "Press Enter to close..."
fi

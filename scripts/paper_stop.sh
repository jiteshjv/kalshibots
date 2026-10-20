#!/bin/bash
pkill -f "paper-supervisor"; pkill -f "scripts/paper_trader.py"; sleep 1
pgrep -f "paper_trader.py|paper-supervisor" >/dev/null && echo "still running?" || echo "stopped"

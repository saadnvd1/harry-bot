#!/bin/bash
# Start Harry Bot — runs bot + worker in one command.
# Usage: ./start.sh [--workers N]
#
# Ctrl+C stops everything cleanly.

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"
WORKERS=1
LOG="$DIR/harry.log"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --workers) WORKERS="$2"; shift 2 ;;
        *) echo "Usage: ./start.sh [--workers N]"; exit 1 ;;
    esac
done

# Check venv
if [ ! -f "$VENV" ]; then
    echo "✗ No venv found. Run: python3 setup.py"
    exit 1
fi

# Check .env
if [ ! -f "$DIR/.env" ]; then
    echo "✗ No .env found. Run: python3 setup.py"
    exit 1
fi

# Read bot username from .env for display
BOT_USERNAME=$(grep '^BOT_USERNAME=' "$DIR/.env" 2>/dev/null | cut -d= -f2)

PIDS=()
NAMES=()

cleanup() {
    echo ""
    echo "Stopping Harry..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait 2>/dev/null
    echo "Done."
}

trap cleanup EXIT INT TERM

cd "$DIR"

# Start bot
$VENV bot.py >> "$LOG" 2>&1 &
BOT_PID=$!
PIDS+=($BOT_PID)
NAMES+=("bot")

# Give the bot a moment to crash on bad credentials
sleep 2

if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "✗ Bot failed to start. Check your Telegram token."
    echo ""
    echo "  Last log lines:"
    tail -5 "$LOG" 2>/dev/null | sed 's/^/    /'
    exit 1
fi
echo "✓ Bot started (PID $BOT_PID)"

# Start worker(s)
for i in $(seq 1 "$WORKERS"); do
    $VENV -m worker.main >> "$LOG" 2>&1 &
    PIDS+=($!)
    NAMES+=("worker-$i")
    echo "✓ Worker $i started (PID $!)"
done

echo ""
echo "Harry is running. Ctrl+C to stop."
if [ -n "$BOT_USERNAME" ]; then
    echo "Message him: https://t.me/$BOT_USERNAME"
fi
echo "Logs: tail -f $LOG"
echo ""

# Monitor children — exit if any die
while true; do
    for idx in "${!PIDS[@]}"; do
        pid="${PIDS[$idx]}"
        name="${NAMES[$idx]}"
        if ! kill -0 "$pid" 2>/dev/null; then
            echo ""
            echo "⚠  $name (PID $pid) exited unexpectedly."
            echo "Check logs: tail -20 $LOG"
            exit 1
        fi
    done
    sleep 5
done

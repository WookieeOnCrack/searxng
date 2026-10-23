#!/bin/bash
set -e

LOG_FILE="/tmp/xvfb.log"

# Clean up legacy X11 lock files
sudo rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

echo "Starting Xvfb on $DISPLAY with resolution $RESOLUTION..."

# Start Xvfb as root but preserve environment variables
sudo -E nohup Xvfb $DISPLAY -ac -screen 0 $RESOLUTION -nolisten tcp +extension RANDR > "$LOG_FILE" 2>&1 &

# Give Xvfb a moment to initialize and write its lockfile
sleep 1.5

# Verify Xvfb process is alive and lockfile exists
if pgrep -x "Xvfb" > /dev/null && [ -f /tmp/.X99-lock ]; then
    echo "🚀 Xvfb is running successfully in the background."
else
    echo "❌ Xvfb failed to start properly!"
    echo "=== LAST 20 LINES OF $LOG_FILE ==="
    tail -n 20 "$LOG_FILE" 2>/dev/null
    exit 1
fi

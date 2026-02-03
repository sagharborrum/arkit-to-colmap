#!/bin/bash
while ps aux | grep -q "[b]rush_app"; do
    osascript -e 'tell application "System Events" to set frontmost of (first process whose name contains "brush") to true' 2>/dev/null
    sleep 30
done

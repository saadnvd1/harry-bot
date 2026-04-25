---
command: status
description: Quick system health check
agent: ollama
---
Run these commands and report a brief status summary:

1. `uptime`
2. `df -h /`
3. `free -h` (if on Linux) or `vm_stat` (if on macOS)
4. `sm list` (if serviceman is installed)

Format as a compact status block. No explanations — just the numbers.

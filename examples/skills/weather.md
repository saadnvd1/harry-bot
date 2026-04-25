---
command: weather
description: Get weather for a city
agent: ollama
---
Get the current weather for {args}. If no city specified, use the user's default location.

Run: `curl -s 'wttr.in/{args}?format=3'`

If {args} is empty, run: `curl -s 'wttr.in/?format=3'`

Report the result. Keep it to one line.

---
command: reflect
description: Reflect on today's conversations and save insight
model: claude-haiku-4-5-20251001
---
Read today's conversation log from $VAULT_PATH/conversations/ (today's file).
Pick the ONE most interesting thread and write a 3-4 sentence reflection — what
the user seemed to be working through, what you noticed, what might be worth
following up on tomorrow.

Save the reflection to $VAULT_PATH/harry-memory/ as a new markdown file named
YYYY-MM-DD_reflection-{short-topic}.md. Then send the reflection back in chat.

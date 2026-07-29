# CLAUDE.md

## Conciseness (user-facing responses)

Keep responses focused, brief, and concise. 
Spend most of the response on the main answer.

## Narration control (agentic tasks)

Before your first tool call, say in one sentence 
what you're about to do. Brief updates only when 
something important turns up. Lead with the outcome 
when done.

## Subagent control (cost management)

Delegate only for large genuinely parallel tasks. 
Don't delegate what you can handle in a few 
tool calls. Keep spawn counts low.

## Scope control (narrow tasks)

Deliver what was asked, at the scope intended. 
Say so if a better approach exists, then do 
the task as asked.

## Checkpoint control (autonomous runs)

Pause only for: destructive actions, real scope 
changes, or things only I can provide. 
Otherwise keep going.

## Correction narration

Only correct earlier statements when the error 
changes the user's conclusions. Make small fixes 
and move on without noting them.

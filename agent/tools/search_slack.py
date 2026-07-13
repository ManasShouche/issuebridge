"""Slack community search tool for the IssueBridge agent."""
import re

from claude_agent_sdk import tool

from agent.context import agent_deps_var
from rts import search_slack


@tool(
    name="search_slack_history_tool",
    description=(
        "Search the vLLM Slack community workspace for prior discussions on a topic. "
        "Use this to find community answers, workarounds, and related threads before answering."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Topic, error, or question to search for in Slack history.",
            }
        },
        "required": ["query"],
    },
)
async def search_slack_history_tool(args: dict) -> dict:
    query = args["query"]

    # Pull the Real-Time Search action_token from the triggering Slack event
    # (required by assistant.search.context).
    action_token: str | None = None
    channel_id = ""
    deps = agent_deps_var.get(None)
    if deps:
        action_token = deps.action_token
        channel_id = deps.channel_id

    results = await search_slack(
        query, limit=5, channel_id=channel_id, action_token=action_token
    )
    if not results:
        text = f"No Slack discussions found for: {query}"
    else:
        name_cache: dict[str, str] = {}
        lines = []
        for r in results:
            label = await _channel_label(deps.client if deps else None, r["channel"], name_cache)
            preview = r["text"][:300].replace("\n", " ")
            link = (
                f"\n  Permalink (cite this): {r['permalink']}"
                if r["permalink"]
                else "\n  (no permalink — from seeded demo data; cite the channel name only)"
            )
            lines.append(f"{label}: {preview}{link}")
            if deps is not None and r["permalink"]:
                deps.citations.append(f"<{r['permalink']}|{label} thread>")
        text = "\n\n".join(lines)
    return {"content": [{"type": "text", "text": text}]}


_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{7,}$")


async def _channel_label(client, channel: str, cache: dict[str, str]) -> str:
    """Resolve a channel ID (e.g. C0BFS4R283Z) to a human-readable #name."""
    if not channel:
        return "unknown channel"
    if not _CHANNEL_ID_RE.match(channel):
        # Already a name (seed data / legacy search), not an ID
        return f"#{channel}"
    if channel in cache:
        return cache[channel]
    label = f"#{channel}"
    if client is not None:
        try:
            info = await client.conversations_info(channel=channel)
            label = f"#{info['channel']['name']}"
        except Exception:
            pass
    cache[channel] = label
    return label

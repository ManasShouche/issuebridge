"""Real-Time Search adapter.

Primary path: Slack `assistant.search.context` API (requires `search:read.public`
scope and an action_token pulled from a live message/app_mention event).
Fallbacks: legacy `search.messages` (user token with `search:read`), then a
local keyword search over the seeded demo conversations.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"


async def search_slack(
    query: str,
    limit: int = 5,
    channel_id: str = "",
    thread_ts: str = "",
    action_token: str | None = None,
) -> list[dict]:
    """Search the Slack workspace for relevant messages.

    Args:
        action_token: Short-lived token from a message/app_mention event payload.
            Required by the Real-Time Search API; without it the RTS call is
            skipped (e.g. poller-triggered searches) and fallbacks are used.

    Returns a list of dicts with keys: text, channel, permalink, ts.
    """
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")

    # Primary: assistant.search.context (Real-Time Search API).
    # The API requires the action_token from the triggering event.
    if bot_token and action_token:
        results = await _assistant_search(bot_token, query, limit, channel_id, action_token)
        if results:
            return results

    # Fallback: legacy search.messages (needs search:read on user token)
    user_token = os.environ.get("SLACK_USER_TOKEN", "")
    if user_token:
        results = await _legacy_search(user_token, query, limit)
        if results:
            return results

    # Last resort: search locally seeded conversations (demo fallback)
    return _local_seed_search(query, limit)


async def _assistant_search(
    token: str,
    query: str,
    limit: int,
    channel_id: str,
    action_token: str,
) -> list[dict]:
    """Call the Slack Real-Time Search API via assistant.search.context.

    Documented args: query (required), action_token (from the triggering
    message/app_mention event), channel_types, content_types,
    context_channel_id, limit.
    """
    payload: dict = {
        "query": query,
        "action_token": action_token,
        "channel_types": "public_channel",
        "content_types": "messages",
        "limit": limit,
    }
    if channel_id:
        payload["context_channel_id"] = channel_id

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{SLACK_API}/assistant.search.context",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        logger.warning(f"assistant.search.context HTTP {resp.status_code}")
        return []

    data = resp.json()
    if not data.get("ok"):
        logger.warning(f"assistant.search.context error: {data.get('error')}")
        return []

    # Response shape: {"ok": true, "results": {"messages": [
    #   {author_user_id, team_id, channel_id, message_ts, content, permalink}, ...]}}
    messages = data.get("results", {}).get("messages", [])
    return [
        {
            "text": m.get("content", m.get("text", "")),
            "channel": m.get("channel_id", _channel_name(m)),
            "permalink": m.get("permalink", ""),
            "ts": m.get("message_ts", m.get("ts", "")),
        }
        for m in messages[:limit]
    ]


async def _legacy_search(user_token: str, query: str, limit: int) -> list[dict]:
    """Dev fallback using the legacy search.messages endpoint."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{SLACK_API}/search.messages",
            params={"query": query, "count": limit, "highlight": False},
            headers={"Authorization": f"Bearer {user_token}"},
        )

    if resp.status_code != 200:
        return []

    data = resp.json()
    if not data.get("ok"):
        logger.warning(f"search.messages error: {data.get('error')}")
        return []

    matches = data.get("messages", {}).get("matches", [])
    return [
        {
            "text": m.get("text", ""),
            "channel": _channel_name(m),
            "permalink": m.get("permalink", ""),
            "ts": m.get("ts", ""),
        }
        for m in matches
    ]


def _channel_name(msg: dict) -> str:
    ch = msg.get("channel", "")
    if isinstance(ch, dict):
        return ch.get("name", "")
    return ch


def _local_seed_search(query: str, limit: int) -> list[dict]:
    """Keyword search over the locally seeded conversations (demo / no-token fallback)."""
    import json
    from pathlib import Path

    seed_file = Path(__file__).parent / "seed" / "conversations.json"
    if not seed_file.exists():
        return []

    try:
        convos = json.loads(seed_file.read_text())
    except Exception:
        return []

    terms = [t.lower() for t in query.split() if len(t) > 2]
    results: list[dict] = []
    seen_texts: set[str] = set()

    for convo in convos:
        channel = convo.get("channel", "")
        for msg in convo.get("thread", []):
            text = msg.get("text", "")
            tl = text.lower()
            score = sum(1 for t in terms if t in tl)
            if score > 0 and text not in seen_texts:
                seen_texts.add(text)
                results.append({
                    "text": text,
                    "channel": channel,
                    "permalink": "",
                    "ts": "",
                    "_score": score,
                })

    results.sort(key=lambda x: -x["_score"])
    for r in results:
        r.pop("_score", None)
    return results[:limit]

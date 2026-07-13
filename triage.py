"""Triage pipeline: new issue → dedupe → card in #maintainers."""
import logging
import os

from slack_sdk.web.async_client import AsyncWebClient

from cards import triage_card
from dedupe import triage_issue
from listeners.actions.triage_buttons import store_verdict

logger = logging.getLogger(__name__)

MAINTAINERS_CHANNEL = os.environ.get("MAINTAINERS_CHANNEL", "maintainers")


async def handle_new_issue(issue: dict, client: AsyncWebClient) -> None:
    """Run the full triage pipeline for a newly detected fork issue."""
    num = issue["number"]
    title = issue.get("title", f"Issue #{num}")
    logger.info(f"Triaging issue #{num}: {title}")

    try:
        verdict = await triage_issue(issue)
        store_verdict(num, verdict)

        blocks = triage_card(issue, verdict)

        # Resolve channel ID
        ch_id = await _resolve_channel(client, MAINTAINERS_CHANNEL)
        if not ch_id:
            logger.error(f"Could not find channel #{MAINTAINERS_CHANNEL} — card not posted")
            return

        await client.chat_postMessage(
            channel=ch_id,
            text=f"New issue #{num}: {title} — verdict: {verdict.get('verdict', 'unknown')}",
            blocks=blocks,
        )
        logger.info(f"Triage card posted for issue #{num}")

    except Exception:
        logger.exception(f"Triage pipeline failed for issue #{num}")


async def _resolve_channel(client: AsyncWebClient, name: str) -> str | None:
    """Resolve a channel name to its ID. Checks env var first (Enterprise Grid safe)."""
    env_key = name.upper().replace("-", "_") + "_CHANNEL_ID"
    from_env = os.environ.get(env_key, "")
    if from_env:
        return from_env

    try:
        cursor = None
        while True:
            resp = await client.conversations_list(
                types="public_channel,private_channel", limit=200, cursor=cursor
            )
            for ch in resp["channels"]:
                if ch["name"] == name:
                    return ch["id"]
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except Exception:
        logger.exception("Failed to resolve channel")
    return None

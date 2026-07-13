"""Handlers for IssueBridge triage card buttons."""
import json
import logging
import re
from pathlib import Path

from slack_bolt import Ack
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.web.async_client import AsyncWebClient

from cards import card_dismissed, card_replied
from github_mcp import post_issue_comment

logger = logging.getLogger(__name__)

_VERDICT_FILE = Path(".verdicts.json")

# Double-click guard: issues currently being posted or already posted (L6-04).
_posting: set[int] = set()


def _load_verdicts() -> dict:
    try:
        if _VERDICT_FILE.exists():
            return json.loads(_VERDICT_FILE.read_text())
    except Exception:
        pass
    return {}


def store_verdict(issue_number: int, verdict: dict) -> None:
    data = _load_verdicts()
    data[str(issue_number)] = verdict
    try:
        _VERDICT_FILE.write_text(json.dumps(data))
    except Exception:
        logger.warning("Could not persist verdict to disk")


def _get_verdict(issue_number: int) -> dict:
    return _load_verdicts().get(str(issue_number), {})


def get_triage_stats() -> dict:
    """Aggregate stats for the App Home dashboard."""
    data = _load_verdicts()
    verdicts = list(data.values())
    return {
        "triaged": len(verdicts),
        "duplicates": sum(1 for v in verdicts if v.get("verdict") in ("duplicate", "related")),
        "replied": sum(1 for v in verdicts if v.get("_replied")),
    }


def _mark_replied(issue_number: int) -> None:
    verdict = _get_verdict(issue_number)
    if verdict:
        verdict["_replied"] = True
        store_verdict(issue_number, verdict)


_PERMALINK_RE = re.compile(r"/archives/([A-Z0-9]+)/p(\d{16})")


async def _notify_cited_thread(
    client: AsyncWebClient, verdict: dict, issue_number: int, comment_url: str
) -> None:
    """Bridge back the other way: tell the cited Slack thread its answer was reused.

    Parses the channel + message ts out of the first cited permalink and posts
    a short note in that thread. Cosmetic — failures are logged, never raised.
    """
    try:
        matches = verdict.get("slack_matches") or []
        permalink = next((m.get("permalink", "") for m in matches if m.get("permalink")), "")
        m = _PERMALINK_RE.search(permalink)
        if not m:
            return
        channel, raw_ts = m.group(1), m.group(2)
        # Permalinks to replies carry ?thread_ts=<parent>; use it so the note
        # lands in the right thread.
        parent = re.search(r"[?&]thread_ts=(\d+\.\d+)", permalink)
        thread_ts = parent.group(1) if parent else f"{raw_ts[:10]}.{raw_ts[10:]}"
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=(
                f":link: This came up again as a new issue — a maintainer just posted "
                f"your answer there: <{comment_url}|issue #{issue_number} comment>"
            ),
        )
    except Exception:
        logger.exception("Bridge-back note to cited Slack thread failed (non-fatal)")


async def handle_post_reply(
    ack: Ack,
    body: dict,
    client: AsyncWebClient,
    context: AsyncBoltContext,
    logger: logging.Logger,
):
    """Post the LLM-suggested reply as a GitHub comment on the fork issue."""
    await ack()

    action = body["actions"][0]
    try:
        action_value = json.loads(action.get("value", "{}"))
        issue_number = int(action_value.get("number", 0))
    except (json.JSONDecodeError, ValueError):
        logger.error(f"Could not parse action value: {action.get('value')}")
        return

    verdict = _get_verdict(issue_number)
    suggested_reply = verdict.get("suggested_reply", "")

    if not suggested_reply:
        await client.chat_postEphemeral(
            channel=context.channel_id,
            user=context.user_id,
            text=":warning: No draft reply found for this issue.",
        )
        return

    # Guard against double-clicks posting duplicate GitHub comments
    if issue_number in _posting:
        return
    _posting.add(issue_number)

    try:
        comment_url = await post_issue_comment(issue_number, suggested_reply)
        logger.info(f"Posted comment on issue #{issue_number}: {comment_url}")
        _mark_replied(issue_number)

        # Update the triage card to show replied status
        original_blocks = body["message"]["blocks"]
        updated_blocks = card_replied(original_blocks, comment_url)
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            blocks=updated_blocks,
            text=f"IssueBridge replied to issue #{issue_number}",
        )

        # Bridge back: let the cited Slack thread know its answer was reused
        await _notify_cited_thread(client, verdict, issue_number, comment_url)

    except Exception:
        # Allow retry after a failure (e.g. bad token fixed)
        _posting.discard(issue_number)
        logger.exception(f"Failed to post reply for issue #{issue_number}")
        await client.chat_postEphemeral(
            channel=context.channel_id,
            user=context.user_id,
            text=":warning: Failed to post the reply to GitHub. Check logs.",
        )


async def handle_show_draft(
    ack: Ack,
    body: dict,
    client: AsyncWebClient,
    context: AsyncBoltContext,
    logger: logging.Logger,
):
    """Open an editable modal with the LLM-generated draft reply."""
    await ack()

    action = body["actions"][0]
    try:
        action_value = json.loads(action.get("value", "{}"))
        issue_number = int(action_value.get("number", 0))
        reply_text = action_value.get("reply", "")
    except (json.JSONDecodeError, ValueError):
        issue_number = 0
        reply_text = ""

    if not reply_text:
        verdict = _get_verdict(issue_number)
        reply_text = verdict.get("suggested_reply", "")

    metadata = json.dumps({
        "number": issue_number,
        "channel": body["channel"]["id"],
        "ts": body["message"]["ts"],
    })

    try:
        await client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "draft_modal_submit",
                "private_metadata": metadata,
                "title": {"type": "plain_text", "text": f"Draft — issue #{issue_number}"[:24]},
                "submit": {"type": "plain_text", "text": "Post to GitHub"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "draft",
                        "label": {"type": "plain_text", "text": "Edit the reply before posting"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "text",
                            "multiline": True,
                            "initial_value": reply_text or "(no draft available)",
                        },
                    }
                ],
            },
        )
    except Exception:
        # Fallback: show the draft as a thread reply if the modal can't open
        logger.exception("views_open failed — falling back to thread message")
        await client.chat_postMessage(
            channel=body["channel"]["id"],
            thread_ts=body["message"]["ts"],
            text=f"*Draft reply for issue #{issue_number}:*\n\n{reply_text or '_(no draft available)_'}",
        )


async def handle_dismiss_triage(
    ack: Ack,
    body: dict,
    client: AsyncWebClient,
    logger: logging.Logger,
):
    """Maintainer overrides the verdict: mark the issue as new, not a duplicate."""
    await ack()

    action = body["actions"][0]
    try:
        issue_number = int(json.loads(action.get("value", "{}")).get("number", 0))
    except (json.JSONDecodeError, ValueError):
        issue_number = 0

    # Record the human override in the persisted verdict
    if issue_number:
        verdict = _get_verdict(issue_number)
        if verdict:
            verdict["verdict"] = "new"
            verdict["_dismissed_by"] = body.get("user", {}).get("id", "")
            store_verdict(issue_number, verdict)

    try:
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            blocks=card_dismissed(body["message"]["blocks"], body.get("user", {}).get("id", "")),
            text=f"Issue #{issue_number} marked as new",
        )
    except Exception:
        logger.exception(f"Failed to update card after dismiss for issue #{issue_number}")


async def handle_draft_submit(
    ack: Ack,
    body: dict,
    client: AsyncWebClient,
    logger: logging.Logger,
):
    """Post the (possibly edited) draft from the modal as a GitHub comment."""
    await ack()

    view = body["view"]
    try:
        meta = json.loads(view.get("private_metadata", "{}"))
        issue_number = int(meta.get("number", 0))
        edited_text = view["state"]["values"]["draft"]["text"]["value"] or ""
    except (json.JSONDecodeError, ValueError, KeyError):
        logger.exception("Could not parse draft modal submission")
        return

    if not edited_text.strip() or not issue_number:
        return

    if issue_number in _posting:
        return
    _posting.add(issue_number)

    try:
        comment_url = await post_issue_comment(issue_number, edited_text)
        logger.info(f"Posted edited comment on issue #{issue_number}: {comment_url}")
        _mark_replied(issue_number)

        # Bridge back: let the cited Slack thread know its answer was reused
        await _notify_cited_thread(client, _get_verdict(issue_number), issue_number, comment_url)

        # Update the original triage card to the replied state
        channel, ts = meta.get("channel"), meta.get("ts")
        if channel and ts:
            try:
                hist = await client.conversations_history(
                    channel=channel, latest=ts, inclusive=True, limit=1
                )
                original_blocks = hist["messages"][0].get("blocks", [])
                await client.chat_update(
                    channel=channel,
                    ts=ts,
                    blocks=card_replied(original_blocks, comment_url),
                    text=f"IssueBridge replied to issue #{issue_number}",
                )
            except Exception:
                # Card update is cosmetic — still confirm in-thread
                logger.exception("Could not update triage card after modal post")
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=ts,
                    text=f":white_check_mark: Reply posted — {comment_url}",
                )
    except Exception:
        _posting.discard(issue_number)
        logger.exception(f"Failed to post edited reply for issue #{issue_number}")
        user_id = body.get("user", {}).get("id")
        channel = meta.get("channel")
        if user_id and channel:
            await client.chat_postEphemeral(
                channel=channel,
                user=user_id,
                text=":warning: Failed to post the reply to GitHub. Check logs.",
            )

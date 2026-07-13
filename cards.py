"""Block Kit triage card builders for IssueBridge."""
import json
import os

FORK = os.environ.get("FORK_REPO", "")
UPSTREAM_BASE = "https://github.com/vllm-project/vllm/issues"


def _confidence_emoji(match: dict) -> str:
    return {"high": ":red_circle:", "medium": ":large_yellow_circle:", "low": ":white_circle:"}.get(
        match.get("confidence", "low"), ":white_circle:"
    )


def triage_card(issue: dict, verdict: dict) -> list[dict]:
    """Build the triage card blocks for a new issue.

    Args:
        issue: The GitHub issue dict from the fork.
        verdict: The dedupe result dict from triage_issue().

    Returns:
        List of Block Kit blocks to post to #maintainers.
    """
    num = issue["number"]
    title = issue.get("title", f"Issue #{num}")[:80]
    fork_url = issue.get("html_url", f"https://github.com/{FORK}/issues/{num}")

    dups = (verdict.get("duplicates") or [])[:2]
    slks = (verdict.get("slack_matches") or [])[:2]
    v = verdict.get("verdict", "new")

    verdict_emoji = {"duplicate": ":repeat:", "related": ":link:", "new": ":new:"}.get(v, ":mag:")

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"New issue #{num}: {title}", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{verdict_emoji} *Verdict:* `{v}`   |   <{fork_url}|View on GitHub>",
            },
        },
    ]

    # Quote a snippet of the new issue body so the card reads standalone
    snippet = " ".join((issue.get("body") or "").split())[:160]
    if snippet:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f">{snippet}{'…' if len(snippet) == 160 else ''}"},
        })

    for i, dup in enumerate(dups):
        dup_url = f"{UPSTREAM_BASE}/{dup['number']}"
        confidence_emoji = _confidence_emoji(dup)
        label = "*Likely duplicate of upstream:*" if i == 0 else "*Also related:*"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{label} "
                    f"<{dup_url}|#{dup['number']}> {confidence_emoji} `{dup.get('confidence', 'low')}`\n"
                    f"_{dup.get('reason', '')}_ "
                ),
            },
        })

    for i, slk in enumerate(slks):
        confidence_emoji = _confidence_emoji(slk)
        permalink = slk.get("permalink", "")
        # Seed-data fallback results carry no permalink — don't render a broken link.
        link_part = f"<{permalink}|View thread>" if permalink else "_(seeded demo data — no permalink)_"
        label = "*Already answered in Slack:*" if i == 0 else "*Also discussed:*"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{label} "
                    f"{link_part} {confidence_emoji} `{slk.get('confidence', 'low')}`\n"
                    f"_{slk.get('reason', '')}_ "
                ),
            },
        })

    if not dups and not slks:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No matching upstream issues or Slack threads found._"},
        })

    # Footer: quantify the work done
    stats = verdict.get("_stats") or {}
    if stats:
        elapsed = stats.get("elapsed_s")
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f":mag: IssueBridge · searched {stats.get('github_searched', 0)} upstream issues"
                    f" + {stats.get('slack_searched', 0)} Slack threads"
                    + (f" in {elapsed}s" if elapsed is not None else "")
                ),
            }],
        })

    # Encode the issue number + suggested reply in button values
    suggested_reply = verdict.get("suggested_reply", "")
    encoded = json.dumps({"number": num, "reply": suggested_reply})[:3000]

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "style": "primary",
                "action_id": "post_reply",
                "text": {"type": "plain_text", "text": "Post cited reply", "emoji": True},
                "value": json.dumps({"number": num}),
                "confirm": {
                    "title": {"type": "plain_text", "text": "Post to GitHub?"},
                    "text": {"type": "mrkdwn", "text": "This will post the suggested reply as a comment on the fork issue."},
                    "confirm": {"type": "plain_text", "text": "Post it"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            },
            {
                "type": "button",
                "action_id": "show_draft",
                "text": {"type": "plain_text", "text": "Show draft reply", "emoji": True},
                "value": encoded,
            },
            {
                "type": "button",
                "action_id": "dismiss_triage",
                "text": {"type": "plain_text", "text": "Not a duplicate", "emoji": True},
                "value": json.dumps({"number": num}),
            },
        ],
    })

    return blocks


def card_dismissed(original_blocks: list[dict], user_id: str) -> list[dict]:
    """Update the card after a maintainer marks the issue as not a duplicate."""
    blocks = [b for b in original_blocks if b.get("type") != "actions"]
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": f":new: Marked as *new* (not a duplicate) by <@{user_id}>",
        }],
    })
    return blocks


def card_replied(original_blocks: list[dict], comment_url: str) -> list[dict]:
    """Update the card to show a replied status (removes action buttons)."""
    blocks = [b for b in original_blocks if b.get("type") != "actions"]
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f":white_check_mark: *Reply posted* — <{comment_url}|View comment on GitHub>",
        },
    })
    return blocks

"""LLM-powered deduplication: ranks GitHub candidates and Slack matches against a new issue."""
import json
import logging
import os
import re

import anthropic

from github_mcp import search_upstream_issues
from rts import search_slack

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _anthropic() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


RANK_PROMPT = """\
You are triaging issues for the vLLM open-source project.

NEW ISSUE:
Title: {title}
Body:
{body}

UPSTREAM GITHUB CANDIDATES (search results from vllm-project/vllm):
{candidates}

SLACK SEARCH RESULTS (from community workspace):
{slack_results}

Return ONLY valid JSON with no markdown fences, no extra text:
{{
  "duplicates": [
    {{"number": <int>, "confidence": "high|medium|low", "reason": "<one sentence>"}}
  ],
  "slack_matches": [
    {{"permalink": "<url>", "confidence": "high|medium|low", "reason": "<one sentence>"}}
  ],
  "verdict": "duplicate|related|new",
  "suggested_reply": "<courteous maintainer reply citing issue numbers and slack threads; 2-4 sentences>"
}}

Rules:
- duplicates: list all candidates that are the same root cause, most confident first (may be empty list)
- slack_matches: list slack threads that already address this issue (may be empty list)
- verdict: "duplicate" if high/medium confidence match found; "related" if low confidence; "new" if no match
- suggested_reply: must cite specific issue #s and/or slack permalinks; written as a helpful maintainer
"""


async def triage_issue(issue: dict) -> dict:
    """Run deduplication for a new fork issue.

    Returns the LLM verdict dict:
    {duplicates, slack_matches, verdict, suggested_reply}
    """
    title = issue.get("title", "")
    body = (issue.get("body") or "")[:1000]

    # Run GitHub and Slack searches in parallel
    import asyncio
    import time
    started = time.monotonic()
    github_results, slack_results = await asyncio.gather(
        _multi_search_github(title, body),
        search_slack(f"{title} vLLM", limit=5),
    )

    candidates_text = _format_candidates(github_results)
    slack_text = _format_slack(slack_results)

    prompt = RANK_PROMPT.format(
        title=title,
        body=body or "(no body)",
        candidates=candidates_text or "(no candidates found)",
        slack_results=slack_text or "(no slack results found)",
    )

    stats = {
        "github_searched": len(github_results),
        "slack_searched": len(slack_results),
    }

    try:
        msg = await _anthropic().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        verdict = json.loads(raw)
    except Exception:
        logger.exception("LLM ranking failed — returning default verdict")
        verdict = {
            "duplicates": [],
            "slack_matches": [],
            "verdict": "new",
            "suggested_reply": "Thank you for filing this issue. We'll review it shortly.",
        }

    stats["elapsed_s"] = round(time.monotonic() - started, 1)
    verdict["_stats"] = stats
    return verdict


async def _multi_search_github(title: str, body: str) -> list[dict]:
    """Run 2 search queries and union results (deduped by issue number)."""
    import asyncio

    queries = [title]
    # Extract error string if body contains a code block or "Error"
    if "Error" in body or "error" in body:
        # Extract first error line as a second query
        for line in body.split("\n"):
            line = line.strip()
            if "Error" in line and len(line) < 200:
                queries.append(line[:120])
                break

    results = await asyncio.gather(*[search_upstream_issues(q, k=6) for q in queries])
    seen: set[int] = set()
    merged: list[dict] = []
    for batch in results:
        for item in batch:
            if item["number"] not in seen:
                seen.add(item["number"])
                merged.append(item)
    return merged[:10]


def _format_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return ""
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. #{c['number']} [{c['state']}] {c['title']}\n   {c['body'][:200]}"
        )
    return "\n\n".join(lines)


def _format_slack(results: list[dict]) -> str:
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, 1):
        channel = f"#{r['channel']}" if r["channel"] else "unknown channel"
        lines.append(f"{i}. {channel}: {r['text'][:300]}\n   permalink: {r['permalink']}")
    return "\n\n".join(lines)

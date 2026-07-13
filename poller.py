"""GitHub issue poller — watches the fork for new issues and triggers triage."""
import asyncio
import logging
import os
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

GH = "https://api.github.com"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def watch_fork(
    on_new_issue: Callable[[dict], Awaitable[None]],
    interval: int = 20,
) -> None:
    """Poll the fork repo for new issues; call on_new_issue for each new one.

    Skips the initial batch so only issues filed after startup trigger triage.
    """
    fork = os.environ.get("FORK_REPO", "")
    if not fork:
        logger.error("FORK_REPO env var not set — poller will not start")
        return

    seen: set[int] = set()
    first_pass = True

    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            try:
                resp = await client.get(
                    f"{GH}/repos/{fork}/issues",
                    params={
                        "state": "open",
                        "sort": "created",
                        "direction": "desc",
                        "per_page": 20,
                    },
                    headers=_headers(),
                )
                resp.raise_for_status()
                issues = resp.json()

                for issue in issues:
                    if "pull_request" in issue:
                        continue
                    num = issue["number"]
                    if num not in seen:
                        seen.add(num)
                        if not first_pass:
                            logger.info(f"New issue detected: #{num} — {issue['title']}")
                            await on_new_issue(issue)

                first_pass = False

            except Exception:
                logger.exception("Poller error — will retry")

            await asyncio.sleep(interval)

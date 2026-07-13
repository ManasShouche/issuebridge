"""GitHub operations via the official GitHub MCP server, with a REST fallback."""
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GH = "https://api.github.com"
UPSTREAM = "vllm-project/vllm"

# Set GITHUB_MCP_URL to use the hosted MCP endpoint (e.g. via Docker on localhost).
# Leave unset to fall back to direct REST calls.
MCP_URL = os.environ.get("GITHUB_MCP_URL", "")


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def search_upstream_issues(query: str, k: int = 8) -> list[dict]:
    """Search upstream vllm-project/vllm issues.

    Returns a list of dicts with keys: number, title, body, state, html_url.
    """
    if MCP_URL:
        return await _mcp_search(query, k)
    return await _rest_search(query, k)


async def _rest_search(query: str, k: int) -> list[dict]:
    q = f"repo:{UPSTREAM} is:issue {query}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{GH}/search/issues",
            params={"q": q, "per_page": k},
            headers=_headers(),
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

    return [
        {
            "number": i["number"],
            "title": i["title"],
            "body": (i.get("body") or "")[:400],
            "state": i["state"],
            "html_url": i["html_url"],
        }
        for i in items
    ]


async def _mcp_call(tool_name: str, arguments: dict) -> dict:
    """Call a tool on the GitHub MCP server over Streamable HTTP.

    Uses the `mcp` client library for the proper initialize handshake, then
    parses the JSON payload from the result's text content block.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

    if result.isError:
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        raise RuntimeError(f"MCP tool {tool_name} failed: {' '.join(texts)[:300]}")

    text = next((c.text for c in result.content if getattr(c, "type", "") == "text"), "")
    return json.loads(text) if text else {}


async def _mcp_search(query: str, k: int) -> list[dict]:
    """Search issues via the GitHub MCP server's search_issues tool."""
    data = await _mcp_call(
        "search_issues",
        {"query": f"repo:{UPSTREAM} is:issue {query}", "perPage": k},
    )

    items = data.get("items", []) if isinstance(data, dict) else data
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "body": (i.get("body") or "")[:400],
            "state": i["state"],
            "html_url": i["html_url"],
        }
        for i in items
    ]


async def post_issue_comment(issue_number: int, body: str) -> str:
    """Post a comment on the fork issue; return the comment HTML URL."""
    fork = os.environ.get("FORK_REPO", "")
    if not fork:
        raise ValueError("FORK_REPO not set")

    if MCP_URL:
        return await _mcp_post_comment(fork, issue_number, body)
    return await _rest_post_comment(fork, issue_number, body)


async def _rest_post_comment(repo: str, issue_number: int, body: str) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{GH}/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("html_url", "")


async def _mcp_post_comment(repo: str, issue_number: int, body: str) -> str:
    owner, name = repo.split("/", 1)
    data = await _mcp_call(
        "add_issue_comment",
        {"owner": owner, "repo": name, "issue_number": issue_number, "body": body},
    )
    return data.get("html_url", "") if isinstance(data, dict) else ""

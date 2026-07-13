"""GitHub issue search tool for the IssueBridge agent."""
from claude_agent_sdk import tool

from agent.context import agent_deps_var
from github_mcp import search_upstream_issues


@tool(
    name="search_github_issues_tool",
    description=(
        "Search the upstream vLLM GitHub issue tracker (vllm-project/vllm) for related issues. "
        "Use this to find existing bugs, feature requests, or known fixes before answering. "
        "Good queries: error messages, config flags, model names."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to search for — error message, feature name, or config flag.",
            }
        },
        "required": ["query"],
    },
)
async def search_github_issues_tool(args: dict) -> dict:
    query = args["query"]
    results = await search_upstream_issues(query, k=5)
    if not results:
        text = f"No issues found in vllm-project/vllm for: {query}"
    else:
        deps = agent_deps_var.get(None)
        lines = []
        for r in results:
            lines.append(
                f"#{r['number']} [{r['state']}] {r['title']}\n"
                f"  {r['html_url']}\n"
                f"  {r['body'][:250].replace(chr(10), ' ')}"
            )
            if deps is not None:
                deps.citations.append(f"<{r['html_url']}|#{r['number']}>")
        text = "\n\n".join(lines)
    return {"content": [{"type": "text", "text": text}]}

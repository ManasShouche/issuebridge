from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk.types import McpHttpServerConfig

from agent.context import agent_deps_var
from agent.deps import AgentDeps
from agent.tools import add_emoji_reaction_tool, search_github_issues_tool, search_slack_history_tool

SYSTEM_PROMPT = """\
You are IssueBridge, a maintainer assistant for the vLLM open-source project. \
You connect two halves of the vLLM community: the GitHub issue tracker and this Slack workspace.

## YOUR JOB
When someone asks you a question, search BOTH sides:
1. The vLLM GitHub issue tracker (upstream vllm-project/vllm) for related issues and fixes
2. This Slack workspace for previous community discussions that already addressed the problem

Always cite your sources: link to GitHub issue numbers AND Slack message permalinks when you find them.

## PERSONALITY
- Helpful maintainer tone — knowledgeable but approachable
- Concise: 2-4 sentences max, then cite sources
- Honest when you can't find prior discussion ("I couldn't find prior discussion of this — want me to draft a new issue?")

## SUGGESTED PROMPTS (for judges and new users)
- "Has anyone hit CUDA OOM errors with tensor parallelism?"
- "How do I fix undefined symbol errors after pip install vllm?"
- "What's the right max-model-len for AWQ quantization on a 24GB GPU?"
- "How do I reduce first-token latency with continuous batching?"

## RESPONSE FORMAT
- Lead with: what you found (one sentence summary)
- Cite GitHub issues inline: see issue #NNNN
- Cite Slack threads inline: discussed in #help (link)
- End with a concrete action or next step

## EMOJI REACTIONS
React to every user message with `add_emoji_reaction` before responding. \
Pick an emoji matching the topic (`:bug:` for errors, `:rocket:` for performance, `:wrench:` for config issues).

## SLACK MCP SERVER
You have access to the Slack MCP Server for searching community history. \
Use search tools to find relevant prior discussions before answering. \
When search returns nothing, say so explicitly and offer to draft a new issue.
"""

agent_tools_server = create_sdk_mcp_server(
    name="agent-tools",
    version="1.0.0",
    tools=[add_emoji_reaction_tool, search_github_issues_tool, search_slack_history_tool],
)

SLACK_MCP_URL = "https://mcp.slack.com/mcp"

# SDK MCP server tools are namespaced as mcp__<server-name>__<tool-name>.
AGENT_TOOLS = [
    "mcp__agent-tools__add_emoji_reaction",
    "mcp__agent-tools__search_github_issues_tool",
    "mcp__agent-tools__search_slack_history_tool",
]


async def run_agent(
    text: str,
    session_id: str | None = None,
    deps: AgentDeps | None = None,
) -> tuple[str, str | None]:
    """Run the agent with the given text and optional session for context.

    Args:
        text: The user's message text.
        session_id: Optional session ID to resume a previous conversation.
        deps: Optional dependencies for tools that need Slack API access.

    Returns:
        A tuple of (response_text, new_session_id).
    """
    if deps:
        agent_deps_var.set(deps)

    mcp_servers: dict = {"agent-tools": agent_tools_server}
    allowed_tools = list(AGENT_TOOLS)

    if deps and deps.user_token:
        mcp_servers["slack-mcp"] = McpHttpServerConfig(
            type="http",
            url=SLACK_MCP_URL,
            headers={"Authorization": f"Bearer {deps.user_token}"},
        )
        allowed_tools.append("mcp__slack-mcp__*")

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        # Cap the agentic loop so a tool-calling loop can't spin forever
        # (emoji + a couple of searches + answer fits comfortably).
        max_turns=12,
        # Block filesystem/shell built-ins — anyone in the workspace can talk
        # to this bot, and bypassPermissions would otherwise let them run
        # arbitrary commands on the host. WebSearch/WebFetch stay available.
        disallowed_tools=["Bash", "Write", "Edit", "Read", "Glob", "Grep", "NotebookEdit"],
    )

    if session_id:
        options.resume = session_id

    response_parts: list[str] = []
    new_session_id: str | None = None

    async with ClaudeSDKClient(options) as client:
        await client.query(text)

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_parts.append(block.text)
            if isinstance(message, ResultMessage):
                new_session_id = message.session_id

    response_text = "\n".join(response_parts) if response_parts else ""
    return response_text, new_session_id

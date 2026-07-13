def build_app_home_view(
    install_url: str | None = None,
    is_connected: bool = False,
    stats: dict | None = None,
) -> dict:
    """Build the App Home Block Kit view.

    Args:
        install_url: OAuth install URL. When provided, the user has not
            connected and will see a link to install.
        is_connected: When ``True``, the user is connected and the MCP
            status section shows as connected.
        stats: Triage stats dict with keys ``triaged``, ``duplicates``,
            ``replied`` — rendered as a small dashboard when present.
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "IssueBridge — your project's two-sided memory",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "I triage new GitHub issues against *both* halves of the vLLM project's "
                    "memory — the upstream issue tracker *and* this Slack workspace — and hand "
                    "maintainers a cited triage card.\n\n"
                    "Send me a *direct message* or *mention me in a channel* to search both sides."
                ),
            },
        },
        {"type": "divider"},
    ]

    if stats and stats.get("triaged"):
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Issues triaged*\n{stats['triaged']}"},
                {"type": "mrkdwn", "text": f"*Duplicates caught*\n{stats['duplicates']}"},
                {"type": "mrkdwn", "text": f"*Cited replies posted*\n{stats['replied']}"},
            ],
        })
        blocks.append({"type": "divider"})

    if is_connected:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\U0001f7e2 *Slack MCP Server is connected.*",
                },
            }
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "The agent can search messages, read channels, and more.",
                    }
                ],
            }
        )
    elif install_url:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"\U0001f534 *Slack MCP Server is disconnected.* <{install_url}|Connect the Slack MCP Server.>",
                },
            }
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "The Slack MCP Server enables the agent to search messages, read channels, and more.",
                    }
                ],
            }
        )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\U0001f534 *Slack MCP Server is disconnected.* <https://github.com/slack-samples/bolt-python-starter-agent/blob/main/claude-agent-sdk/README.md#slack-mcp-server|Learn how to enable the Slack MCP Server.>",
                },
            }
        )
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "The Slack MCP Server enables the agent to search messages, read channels, and more.",
                    }
                ],
            }
        )

    return {
        "type": "home",
        "blocks": blocks,
    }

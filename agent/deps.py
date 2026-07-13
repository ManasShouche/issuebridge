from dataclasses import dataclass, field

from slack_sdk.web.async_client import AsyncWebClient


@dataclass
class AgentDeps:
    client: AsyncWebClient
    user_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    user_token: str | None = None
    # Short-lived Real-Time Search token from the triggering message/app_mention
    # event payload; required by assistant.search.context.
    action_token: str | None = None
    # Citations collected by search tools during this run, rendered as a
    # "Sources" context block under the agent's reply. mrkdwn link strings.
    citations: list[str] = field(default_factory=list)

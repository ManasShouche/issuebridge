import asyncio
import re
from logging import Logger

from slack_bolt.context.async_context import AsyncBoltContext
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.say_stream.async_say_stream import AsyncSayStream
from slack_bolt.context.set_status.async_set_status import AsyncSetStatus
from slack_sdk.web.async_client import AsyncWebClient

from agent import AgentDeps, run_agent
from thread_context import session_store
from listeners.events.dedup import already_handled
from listeners.views.feedback_builder import build_feedback_blocks


async def handle_app_mentioned(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    event: dict,
    logger: Logger,
    say: AsyncSay,
    say_stream: AsyncSayStream,
    set_status: AsyncSetStatus,
):
    """Handle @mentions in channels."""
    try:
        channel_id = context.channel_id
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        # Guard against Slack event retries / double delivery
        if already_handled(channel_id, event["ts"]):
            return

        # Strip the bot mention from the text
        cleaned_text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if not cleaned_text:
            await say(
                text="Hey there! How can I help you? Ask me anything and I'll do my best.",
                thread_ts=thread_ts,
            )
            return

        # Set assistant thread status with loading messages
        await set_status(
            status="Checking both sides of the project's memory…",
            loading_messages=[
                "Searching vllm-project/vllm issues…",
                "Searching #help history via Real-Time Search…",
                "Ranking candidates from both sources…",
                "Drafting a cited answer…",
            ],
        )

        # Get session ID for conversation context
        existing_session_id = session_store.get_session(channel_id, thread_ts)

        # Run the agent with deps for tool access
        deps = AgentDeps(
            client=client,
            user_id=context.user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=event["ts"],
            user_token=context.user_token,
            action_token=event.get("action_token"),
        )
        try:
            response_text, new_session_id = await asyncio.wait_for(
                run_agent(cleaned_text, session_id=existing_session_id, deps=deps),
                timeout=180,
            )
        except asyncio.TimeoutError:
            logger.error("Agent run timed out after 180s")
            await say(
                text=":hourglass_flowing_sand: That took longer than expected and I had to stop. Please try again — a shorter or more specific question helps.",
                thread_ts=thread_ts,
            )
            return

        # Stream response in thread with sources footer + feedback buttons
        streamer = await say_stream()
        await streamer.append(markdown_text=response_text)
        blocks = []
        if deps.citations:
            unique = list(dict.fromkeys(deps.citations))[:5]
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f":books: Sources: {' · '.join(unique)}"}],
            })
        blocks.extend(build_feedback_blocks())
        await streamer.stop(blocks=blocks)

        # Store session ID for future context
        if new_session_id:
            session_store.set_session(channel_id, thread_ts, new_session_id)

    except Exception as e:
        logger.exception(f"Failed to handle app mention: {e}")
        await say(
            text=f":warning: Something went wrong! ({e})",
            thread_ts=event.get("thread_ts") or event["ts"],
        )

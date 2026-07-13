import asyncio
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


async def handle_message(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    event: dict,
    logger: Logger,
    say: AsyncSay,
    say_stream: AsyncSayStream,
    set_status: AsyncSetStatus,
):
    """Handle messages sent to the agent via DM or in threads the bot is part of."""
    # Skip message subtypes (edits, deletes, etc.) and bot messages.
    if event.get("subtype"):
        return
    if event.get("bot_id"):
        return

    is_dm = event.get("channel_type") == "im"
    is_thread_reply = event.get("thread_ts") is not None

    if is_dm:
        pass
    elif is_thread_reply:
        # Bot mentions in channels fire an `app_mention` event too — let that
        # handler deal with them (it strips the mention from the text).
        bot_user_id = getattr(context, "bot_user_id", None)
        if bot_user_id and f"<@{bot_user_id}>" in event.get("text", ""):
            return
        # Channel thread replies are handled only if the bot is already engaged
        session = session_store.get_session(context.channel_id, event["thread_ts"])
        if session is None:
            return
    else:
        # Top-level channel messages are handled by app_mentioned
        return

    # Guard against Slack event retries / double delivery
    if already_handled(context.channel_id, event["ts"]):
        return

    try:
        channel_id = context.channel_id
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        # Get session ID for conversation context
        existing_session_id = session_store.get_session(channel_id, thread_ts)

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

        # Run the agent with deps for tool access
        user_id = context.user_id
        deps = AgentDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=event["ts"],
            user_token=context.user_token,
            action_token=event.get("action_token"),
        )
        try:
            response_text, new_session_id = await asyncio.wait_for(
                run_agent(text, session_id=existing_session_id, deps=deps),
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
        logger.exception(f"Failed to handle message: {e}")
        await say(
            text=f":warning: Something went wrong! ({e})",
            thread_ts=event.get("thread_ts") or event.get("ts"),
        )

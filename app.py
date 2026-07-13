import asyncio
import logging
import os

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from listeners import register_listeners
from poller import watch_fork
from triage import handle_new_issue

load_dotenv(dotenv_path=".env", override=False)

logging.basicConfig(level=logging.INFO)
# Quiet the per-request noise from the 20s fork poller and HTTP clients;
# our own INFO logs (new issue detected, card posted, RTS errors) stay visible.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _bootstrap_env() -> None:
    """Write CLI-injected tokens back to .env so standalone scripts can use them.

    Replaces empty KEY= lines in .env with the actual values injected by `slack run`.
    """
    env_path = ".env"
    keys_to_capture = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "GITHUB_TOKEN", "FORK_REPO"]
    try:
        content = open(env_path).read()
        changed = False
        for key in keys_to_capture:
            val = os.environ.get(key, "")
            if val and f"{key}=\n" in content:
                content = content.replace(f"{key}=\n", f"{key}={val}\n", 1)
                changed = True
        if changed:
            open(env_path, "w").write(content)
            logging.getLogger(__name__).info("Bootstrapped .env with tokens from slack run")
    except Exception:
        pass


_bootstrap_env()

# Log SLACK/TEAM env vars injected by slack run so we can find workspace team_id
_slack_env = {k: (v[:20] + "...") if len(v) > 20 else v
              for k, v in os.environ.items() if any(x in k for x in ("SLACK", "TEAM", "WORKSPACE"))}
logging.getLogger(__name__).info(f"Injected env: {_slack_env}")

app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    client=AsyncWebClient(
        base_url=os.environ.get("SLACK_API_URL", "https://slack.com/api"),
        token=os.environ.get("SLACK_BOT_TOKEN"),
    ),
)

register_listeners(app)

logger = logging.getLogger(__name__)


async def _seed_after_connect() -> None:
    """Run seed using the app's client after Socket Mode is established."""
    import json, time
    from pathlib import Path

    conv_path = Path("seed/conversations.json")
    convos = json.loads(conv_path.read_text())

    client = app.client

    # Resolve channel IDs using the connected bot's client
    async def channel_id(name: str) -> str | None:
        # Env var override — bypasses all Enterprise Grid listing restrictions
        env_key = name.upper().replace("-", "_") + "_CHANNEL_ID"
        from_env = os.environ.get(env_key, "")
        if from_env:
            logger.info(f"#{name} from env ({env_key}): {from_env}")
            return from_env

        # Try admin.conversations.search first (works for Enterprise Grid org-wide apps)
        try:
            resp = await client.api_call(
                "admin.conversations.search",
                http_verb="GET",
                params={"query": name, "limit": 5},
            )
            if resp.get("ok"):
                for ch in resp.get("conversations", []):
                    if ch.get("name") == name:
                        logger.info(f"Found #{name} via admin.conversations.search: {ch['id']}")
                        return ch["id"]
        except Exception as e:
            logger.debug(f"admin.conversations.search failed: {e}")

        # Fallback: try conversations.list with team_id
        try:
            auth = await client.auth_test()
            team_id = auth.get("team_id", "")
        except Exception:
            team_id = ""

        cursor = None
        while True:
            kwargs: dict = {"types": "public_channel", "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            if team_id:
                kwargs["team_id"] = team_id
            try:
                resp = await client.conversations_list(**kwargs)
                for ch in resp["channels"]:
                    if ch["name"] == name:
                        return ch["id"]
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except Exception as e:
                logger.error(f"conversations_list failed: {e}")
                break

        # Last resort: try to create the channel
        try:
            kwargs2: dict = {"name": name}
            if team_id:
                kwargs2["team_id"] = team_id
            resp = await client.conversations_create(**kwargs2)
            if resp.get("ok"):
                cid = resp["channel"]["id"]
                logger.info(f"Created #{name}: {cid}")
                return cid
        except Exception as e:
            logger.error(f"conversations_create failed for #{name}: {e}")

        return None

    needed = {c["channel"] for c in convos}
    ids: dict[str, str] = {}
    for name in needed:
        cid = await channel_id(name)
        if cid:
            ids[name] = cid
            logger.info(f"  #{name} -> {cid}")
        else:
            logger.warning(f"  #{name} not found — skipping")

    if not ids:
        logger.error("No channels found. Create #help and #general in Slack first.")
        return

    total = 0
    for convo in convos:
        ch_name = convo["channel"]
        if ch_name not in ids:
            continue
        ch_id = ids[ch_name]
        parent_ts = None

        for msg in convo["thread"]:
            kwargs = {
                "channel": ch_id,
                "text": msg["text"],
                "username": msg["persona"],
                "icon_emoji": msg.get("icon", ":bust_in_silhouette:"),
            }
            if parent_ts:
                kwargs["thread_ts"] = parent_ts
            try:
                resp = await client.chat_postMessage(**kwargs)
                if parent_ts is None:
                    parent_ts = resp["ts"]
                total += 1
                logger.info(f"[{ch_name}] {msg['persona']}: {msg['text'][:50].replace(chr(10),' ')}...")
                await asyncio.sleep(1.2)
            except Exception as e:
                logger.error(f"post failed: {e}")
                await asyncio.sleep(2)

    logger.info(f"Seeding complete: {total} messages across {len(convos)} conversations.")


async def main():
    handler = AsyncSocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))

    poller_task = asyncio.create_task(
        watch_fork(lambda issue: handle_new_issue(issue, app.client))
    )

    seed_task = None
    if os.environ.get("SEED_ON_START", "").lower() == "true":
        logger.info("SEED_ON_START=true — will seed after connection is ready")
        # Small delay to let Socket Mode handshake complete
        async def _delayed_seed():
            await asyncio.sleep(3)
            await _seed_after_connect()
        seed_task = asyncio.create_task(_delayed_seed())

    try:
        await handler.start_async()
    finally:
        poller_task.cancel()
        if seed_task:
            seed_task.cancel()
        for t in [poller_task, seed_task]:
            if t:
                try:
                    await t
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    asyncio.run(main())

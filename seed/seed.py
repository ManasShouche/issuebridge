"""Seed the Slack workspace with community conversation history.

Run once after channels are created:
    python seed/seed.py
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=False)

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
user_token = os.environ.get("SLACK_USER_TOKEN", "")

if not bot_token and not user_token:
    print("ERROR: Set SLACK_BOT_TOKEN or SLACK_USER_TOKEN in .env")
    sys.exit(1)

has_bot_token = bot_token.startswith("xoxb")

bot_client = WebClient(token=bot_token) if bot_token else None
user_client = WebClient(token=user_token) if user_token else None
# Primary client for posting: prefer bot (has chat:write.customize)
post_client = bot_client or user_client


def channel_id(name: str) -> str | None:
    """Resolve channel name -> ID.

    First checks env vars (HELP_CHANNEL_ID, GENERAL_CHANNEL_ID, MAINTAINERS_CHANNEL_ID)
    so this works on Enterprise Grid where API channel listing is blocked.
    """
    # Env var override — set these from right-clicking a channel in Slack → Copy link
    env_key = name.upper().replace("-", "_") + "_CHANNEL_ID"
    from_env = os.environ.get(env_key, "")
    if from_env:
        print(f"    [env] #{name} -> {from_env}")
        return from_env

    # conversations.list with user token
    if user_client:
        try:
            cursor = None
            while True:
                resp = user_client.conversations_list(
                    types="public_channel,private_channel",
                    limit=200,
                    cursor=cursor,
                )
                for ch in resp["channels"]:
                    if ch["name"] == name:
                        print(f"    [user.list] #{name} -> {ch['id']}")
                        return ch["id"]
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except Exception as e:
            print(f"    user conversations.list: {e}")

    # conversations.list with bot token
    if bot_client:
        try:
            auth = bot_client.auth_test()
            team_id = auth.get("team_id", "")
        except Exception:
            team_id = ""
        try:
            cursor = None
            kwargs: dict = {"types": "public_channel", "limit": 200}
            if team_id:
                kwargs["team_id"] = team_id
            while True:
                resp = bot_client.conversations_list(**kwargs)
                for ch in resp["channels"]:
                    if ch["name"] == name:
                        print(f"    [bot.list] #{name} -> {ch['id']}")
                        return ch["id"]
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except Exception as e:
            print(f"    bot conversations.list: {e}")

    return None


def seed():
    conv_path = Path(__file__).parent / "conversations.json"
    convos = json.loads(conv_path.read_text())

    needed = {c["channel"] for c in convos}
    ids = {}
    for name in needed:
        print(f"  Resolving #{name}...")
        cid = channel_id(name)
        if cid:
            ids[name] = cid
        else:
            print(f"  WARNING: #{name} not found — skipping")

    if not ids:
        print("\nNo channels resolved. Options:")
        print("  1. Create #help and #general in Slack, invite @IssueBridge, then re-run")
        print("  2. Set HELP_CHANNEL_ID=C... GENERAL_CHANNEL_ID=C... in .env to bypass lookup")
        return

    # Join channels (bot must be in them to post)
    if bot_client:
        for name, cid in ids.items():
            try:
                bot_client.conversations_join(channel=cid)
                print(f"  Joined #{name}")
            except Exception as e:
                print(f"  Join #{name}: {e}")

    bot_missing: set[str] = set()
    total = 0
    for convo in convos:
        ch_name = convo["channel"]
        if ch_name not in ids:
            print(f"  Skipping #{ch_name} (channel not found)")
            continue
        if ch_name in bot_missing:
            continue

        ch_id = ids[ch_name]
        parent_ts = None

        for msg in convo["thread"]:
            kwargs: dict = {
                "channel": ch_id,
                "text": msg["text"],
            }
            if parent_ts:
                kwargs["thread_ts"] = parent_ts

            if has_bot_token:
                kwargs["username"] = msg["persona"]
                kwargs["icon_emoji"] = msg.get("icon", ":bust_in_silhouette:")

            try:
                resp = post_client.chat_postMessage(**kwargs)
                if parent_ts is None:
                    parent_ts = resp["ts"]
                total += 1
                print(f"  [{ch_name}] {msg['persona']}: {msg['text'][:60].replace(chr(10), ' ')}...")
                time.sleep(1.2)
            except SlackApiError as e:
                err = e.response.get("error", "unknown")
                if err in ("not_allowed_token_type", "missing_scope") and "username" in kwargs:
                    kwargs.pop("username", None)
                    kwargs.pop("icon_emoji", None)
                    try:
                        resp = post_client.chat_postMessage(**kwargs)
                        if parent_ts is None:
                            parent_ts = resp["ts"]
                        total += 1
                        print(f"  [{ch_name}] {msg['text'][:50].replace(chr(10), ' ')}...")
                        time.sleep(1.2)
                    except SlackApiError as e2:
                        print(f"  ERROR: {e2.response.get('error')} — skipping")
                elif err == "not_in_channel":
                    print(f"  Bot not in #{ch_name} — run /invite @IssueBridge in Slack then re-run seed")
                    bot_missing.add(ch_name)
                    break
                else:
                    print(f"  ERROR: {err} — skipping")
                    time.sleep(2)

    print(f"\nSeeded {total} messages across {len(convos)} conversations.")


if __name__ == "__main__":
    seed()

"""Pre-flight check: verifies all required tokens and channels before seeding.

Run: python check_setup.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv(".env", override=False)


def check(label, value, hint=""):
    ok = bool(value and value.strip())
    status = "OK" if ok else "MISSING"
    print(f"  [{status}] {label}" + (f"\n         {hint}" if not ok else ""))
    return ok


def main():
    print("\n=== IssueBridge setup check ===\n")

    ok = True
    ok &= check("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY"),
                "Add to .env")
    ok &= check("SLACK_BOT_TOKEN", os.environ.get("SLACK_BOT_TOKEN"),
                "Run `slack run`, wait 10s for app.py to bootstrap it, then Ctrl+C")
    ok &= check("SLACK_APP_TOKEN", os.environ.get("SLACK_APP_TOKEN"),
                "Same — bootstrapped automatically by app.py on first `slack run`")
    ok &= check("GITHUB_TOKEN", os.environ.get("GITHUB_TOKEN"),
                "Create at github.com/settings/tokens (classic, repo+public_repo scope)")
    ok &= check("FORK_REPO", os.environ.get("FORK_REPO"),
                "Your vllm fork — format: username/vllm")
    check("SLACK_USER_TOKEN", os.environ.get("SLACK_USER_TOKEN"),
          "Optional: user token for legacy search.messages fallback")
    check("GITHUB_MCP_URL", os.environ.get("GITHUB_MCP_URL"),
          "Optional: GitHub MCP server endpoint (REST fallback used when unset)")

    if not ok:
        print("\nFix MISSING items above, then re-run.\n")
        sys.exit(1)

    print("\n--- Testing Slack bot token ---")
    try:
        from slack_sdk import WebClient
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        auth = client.auth_test()
        team_id = auth.get("team_id")
        print(f"  [OK] Connected as @{auth['bot_id']} in workspace: {auth.get('url', '')}")
    except Exception as e:
        print(f"  [FAIL] {e}")
        sys.exit(1)

    print("\n--- Checking required channels ---")
    # Enterprise Grid blocks conversations.list for non-org apps, so verify the
    # channels via their env-var IDs (same overrides triage.py uses).
    for name in ["help", "general", "maintainers", "start-here"]:
        env_key = f"{name.upper().replace('-', '_')}_CHANNEL_ID"
        ch_id = os.environ.get(env_key, "")
        if not ch_id:
            print(f"  [MISSING] #{name} — set {env_key} in .env (right-click channel → copy link for the ID)")
            ok = False
            continue
        try:
            info = client.conversations_info(channel=ch_id)
            actual = info["channel"]["name"]
            member = info["channel"].get("is_member", False)
            note = "" if member else "  [WARN] bot is not a member — run /invite @IssueBridge"
            print(f"  [OK] #{actual} ({ch_id}){note}")
        except Exception as e:
            print(f"  [FAIL] #{name} ({ch_id}): {e}")
            ok = False

    print("\n--- Testing GitHub token ---")
    try:
        import httpx
        fork = os.environ.get("FORK_REPO", "")
        resp = httpx.get(
            f"https://api.github.com/repos/{fork}",
            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                     "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            issues_enabled = data.get("has_issues", False)
            print(f"  [OK] Fork found: {data['full_name']}")
            if not issues_enabled:
                print("  [WARN] Issues are DISABLED on the fork — enable at Settings → Features → Issues")
        else:
            print(f"  [FAIL] HTTP {resp.status_code}: {resp.text[:200]}")
            ok = False
    except Exception as e:
        print(f"  [FAIL] {e}")
        ok = False

    if ok:
        print("\nAll checks passed! Run: python seed/seed.py\n")
    else:
        print("\nFix MISSING/FAIL items above, then re-run.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

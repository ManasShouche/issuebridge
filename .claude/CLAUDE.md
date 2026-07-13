# CLAUDE.md

Guidance for AI coding assistants working in this repository.

## What this is

**IssueBridge** — a Slack Agent Builder Challenge entry (New Slack Agent track, submitted July 2026). A maintainer agent that triages new GitHub issues against *both* the upstream `vllm-project/vllm` tracker and a Slack community workspace, citing both sides. Claimed technologies: **Slack Real-Time Search API** and **MCP**. Judging runs through Aug 6, 2026 — the deployed bot must stay up and the sandbox must not be restructured during that window.

## Architecture (two halves)

1. **Interactive agent** (`app.py` → `listeners/events/*` → `agent/agent.py`): Claude Agent SDK (`ClaudeSDKClient`), model `claude-haiku-4-5`, Socket Mode. Custom tools on an in-process SDK MCP server: `add_emoji_reaction`, `search_github_issues_tool`, `search_slack_history_tool`. Sessions resumed per `(channel, thread_ts)` via `thread_context/store.py` (in-memory).
2. **Triage pipeline** (`poller.py` → `dedupe.py` → `cards.py` → `triage.py` → `listeners/actions/triage_buttons.py`): does NOT use the Agent SDK — raw `anthropic.AsyncAnthropic` (Haiku) ranks candidates. Poller checks the fork every 20s and **skips issues that existed at startup** — file demo issues only while the bot is running.

## Non-obvious constraints (learned the hard way)

- **Real-Time Search** (`rts.py`): `assistant.search.context` requires an `action_token` extracted from a *live* message/app_mention event (`event["action_token"]` → `AgentDeps.action_token`). Poller-triggered searches have no token and fall back (legacy `search.messages` → local seed keyword search; seed results have **no permalinks**). Requires `search:read.public` bot scope — reinstall the app after scope changes.
- **Enterprise Grid sandbox**: `conversations.list`, `users.conversations`, `conversations.create` are blocked (`team_access_not_granted`) even with `team_id`. Always use the `*_CHANNEL_ID` env vars (HELP/GENERAL/MAINTAINERS/START_HERE). `conversations.info` works fine.
- **GitHub tokens**: fine-grained PATs need **Issues: Read and write** on the fork or `post_issue_comment` 403s ("Resource not accessible"). Fine-grained PATs expire — check the date.
- **GitHub MCP path** (`github_mcp.py`): real MCP client (via `mcp` package, Streamable HTTP) used only when `GITHUB_MCP_URL` is set; otherwise plain REST. Tool names: `search_issues` (`perPage` camelCase), `add_issue_comment`.
- **Agent safety rails**: `permission_mode="bypassPermissions"` would expose built-in Bash/file tools to anyone in the workspace — `disallowed_tools` blocks them (WebSearch/WebFetch stay). `max_turns=12` + 180s `asyncio.wait_for` in both handlers prevent stuck runs.
- **Event dedup** (`listeners/events/dedup.py`): mentions fire both `app_mention` and `message`; Slack also retries slow events. The guard keys on `(channel, ts)`; `message.py` additionally skips channel messages containing a bot mention.
- **Claude Agent SDK bundles its CLI** (`claude_agent_sdk/_bundled/claude`, ~226MB, platform-specific wheel) — no Node.js needed anywhere.
- **Seeding**: `seed/conversations.json` (17 threads, 13 mapped to real closed vLLM issues) posted via `chat.postMessage` with `username`/`icon_emoji` persona overrides. Messages cannot be backdated.

## State & persistence

- `.verdicts.json` (gitignored): triage verdicts + `_replied`/`_dismissed_by` flags; feeds App Home stats and "Post cited reply". Lost on redeploy — old cards' buttons die.
- Session store is in-memory — restarts lose conversation continuity.

## Running

- `python check_setup.py` — pre-flight (tokens, channels via env IDs, fork).
- Local dev: `slack run` (uses the **dev app**, name shows "IssueBridge (local)"). Production: `python3 app.py` with tokens in `.env`.
- **Only one instance at a time** (local vs deployed) — both connect Socket Mode to the same app and split events.
- Production runs on an Azure VM under systemd (`issuebridge.service`, `Restart=always`, enabled on boot). Deployment specifics (IP, resource group, SSH) are deliberately not in this public repo — kept in the operator's local notes.
- `.env` is gitignored and was never committed; it is copied to the deploy host via `scp` only. Rotating a token means updating **both** the local and VM copies + `systemctl restart issuebridge`.

## Slack apps

Two apps exist: the CLI **dev app** (suffix "(local)", used by `slack run`) and a **prod app** (clean "IssueBridge" name, in `.slack/apps.json`). The "(local)" suffix on dev apps is CLI-enforced and cannot be removed; switching the deployed bot to the prod app = `slack install`, swap `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`, re-invite to channels.

## Demo / judging assets

- Judge instructions pinned in `#start-here`; suggested prompts match seeded content.
- Reserved demo issues (with matching seeded threads): see the operator's `issuebridge-demo-issues.md` (kept out of the repo).
- `assets/`: app icon, architecture diagram, 3:2 Devpost thumbnail — all original art (no third-party logos; vLLM's logo is trademarked).

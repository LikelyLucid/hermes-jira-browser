# Hermes Jira Browser

A native Jira workspace for Hermes Desktop. It presents Jira issues on a Kanban-style route, keeps issue details in a resizable drawer, and connects Jira tickets to real Hermes projects, worktrees, and sessions without using Kanban dispatch.

## Features

- Defaults to the current Jira user's active assigned issues.
- Discovers Jira workflow lanes, including empty lanes, from available transitions.
- Supports saved JQL views, pagination, stale-while-revalidate caches, and an attention view.
- Shows full issue metadata, descriptions, comments, parent/subtasks, components, versions, and attachments.
- Renders authenticated image thumbnails without exposing Jira credentials to the Desktop renderer.
- Maps Jira projects to Hermes Projects and creates or reuses issue worktrees.
- Links one worktree to a ticket and automatically discovers sessions rooted in it.
- Persists each chat link with its Desktop connection and Hermes profile owner, so identical stored session ids remain distinct across profiles.
- Supports explicit **Resume work**, **New chat**, chat attachment, and chat unlinking.
- Highlights a ticket card with a live native status while linked Hermes sessions are working, waiting for input, starting, idle, failed, or archived.
- Mirrors linked-session status in the native Hermes status bar/title bar, with a palette command and rebindable **Open Jira Browser** keybind (`⌘⇧J` / `Ctrl+Shift+J`).
- Keeps completion and needs-input notifications opt-in from the command palette and deduplicated across repeated live updates.
- Shows an owner-qualified Jira context strip in the native composer when the focused chat is linked, with explicit bounded insertion actions for one comment or attachment metadata; insertion never submits a message.
- Loads linked issue data through a shared, scope-qualified query cache in bounded batches of at most 50 issue keys, with a compatibility fallback to per-ticket reads.
- Deep-links exact tickets with `/jira?issue=PROJECT-123`; the in-route drawer follows hash navigation.
- Pins a read-only ticket companion beside chat when the Desktop `host.openWorkspace` contract is available, without opening a session.
- Provides read-only repository, branch, diff, commit, ahead/behind, pull-request, and CI context for a ticket when its mapped Jira worktree exists.
- Drafts Jira updates in a normal Hermes session; posting remains explicit.
- Suggests transitions but never applies one without a click.
- Persists idempotency receipts for explicit Jira comments and transitions so a retried request cannot duplicate a completed write.
- Exposes four read-only agent tools: assigned issues, bounded JQL search, fresh issue detail, and available transitions.
- Agent tool results are treated as untrusted Jira reference data; no write or attachment-download tool is registered.
- Stores human-editable, credential-free settings as JSON.

## Security model

- Jira credentials remain in the Python backend and are never written to Desktop storage, plugin settings, caches, prompts, or logs.
- Jira Basic Auth is allowed only over HTTPS.
- Authenticated redirects are refused so credentials cannot be forwarded to another host.
- Jira descriptions and comments are treated as untrusted data.
- Attachment metadata is validated against the requested issue before a bounded thumbnail is fetched.
- Worktree creation is noninteractive and disables repository hooks and injected global Git configuration.
- Repository context is resolved only from the server-side Jira project mapping and the canonical `jira/<ISSUE-KEY>` worktree; renderer-supplied paths are ignored.
- Repository and `gh` commands use argv execution, closed stdin, noninteractive environments, timeouts, and bounded output. GitHub context is unavailable when `gh` is missing, unauthenticated, or no pull request exists.
- Unlinking a Jira association never deletes or archives the Hermes session.

See [SECURITY.md](SECURITY.md) for reporting guidance and operational notes.

## Requirements

- Hermes Agent with Dashboard plugin support.
- Hermes Desktop with runtime Desktop plugin support.
- Python 3.11 or newer.
- Git.
- A Jira Cloud account and API token.

## Installation

Choose the Hermes home used by your active profile:

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
```

Install the unified plugin source:

```bash
git clone https://github.com/OWNER/hermes-jira-browser.git \
  "$HERMES_HOME/plugins/jira-browser"
```

For Desktop versions that do not automatically reconcile a unified plugin's Desktop half, copy the runtime entry explicitly:

```bash
mkdir -p "$HERMES_HOME/desktop-plugins/jira-browser"
cp "$HERMES_HOME/plugins/jira-browser/desktop/plugin.js" \
  "$HERMES_HOME/desktop-plugins/jira-browser/plugin.js"
```

The Desktop JavaScript entry hot-reloads when edited. Loading or changing Python API routes requires a normal Hermes backend restart.

## Jira configuration

Set `HERMES_JIRA_CONFIG_FILE` to a JSON file containing:

```json
{
  "baseUrl": "https://your-site.atlassian.net",
  "email": "you@example.com",
  "apiToken": "your-jira-api-token"
}
```

`token` is accepted as an alias for `apiToken`. If `HERMES_JIRA_CONFIG_FILE` is unset, the plugin reads `~/jira-config/config.json` for compatibility with existing Jira CLI setups.

Environment variables override file values:

```bash
export JIRA_BASE_URL="https://your-site.atlassian.net"
export JIRA_EMAIL="you@example.com"
export JIRA_API_TOKEN="..."
```

Do not commit this configuration file. The repository's `.gitignore` excludes common local credential and state files.

## First use

1. Open **Jira** from the Hermes sidebar.
2. Open a ticket and map its Jira project to a Hermes Project/repository.
3. Optionally link an existing worktree.
4. Choose:
   - **Open work session** for the first session in the ticket worktree.
   - **Resume work** to reopen a linked session.
   - **New chat** to create another session in the same linked worktree.
5. Use the native composer attachment menu for **Insert Jira comment into composer** or **Insert Jira attachment metadata into composer**. These actions add bounded, explicitly labelled untrusted reference text to the draft and never send it.
6. Use **Scan chats** to discover related sessions. Exact linked-worktree matches attach automatically unless you explicitly unlinked that session.

## Settings

Settings are written atomically with mode `0600` to:

```text
$HERMES_HOME/jira-browser/settings.json
```

The drawer provides a form for common settings and a raw JSON editor for complete control. Settings include saved views, JQL, default view, page size, base ref, grouping, and UI preferences. Credentials are never stored there.

## Desktop compatibility

The native companion uses the optional `host.openWorkspace` SDK method and falls back to a warning on older Desktop builds. The SDK's palette contract currently provides a no-argument `run` callback but no safe prompt or ticket-picker surface, so **Open Jira ticket…** is intentionally not registered rather than relying on an invented API.

## Repository context API

The read-only endpoint:

```text
GET /issues/{issue_key}/repository-context?base_ref=main
```

The backend resolves the repository from the stored Jira project mapping and then checks the canonical `.worktrees/jira-{ISSUE-KEY}` worktree. It returns an explicit `unavailable` state when the mapping, repository, worktree, Git base ref, or GitHub CLI context is absent. The optional `base_ref` is validated before Git compares ahead/behind state. No renderer-provided filesystem path is accepted.

The bounded issue batch endpoint is used for board link/status refreshes:

```text
POST /issues/batch
{"issue_keys":["PROJECT-123"],"include_transitions":false,"include_links":true,"include_details":false,"connection_id":"local","profile_name":"default","target_profile":"default"}
```

The request accepts at most 50 validated issue keys, deduplicates keys, limits concurrent Jira work, and scopes returned links by Jira origin plus connection/source-profile/target-profile owner. The focused-chat context endpoint requires the complete owner tuple and returns bounded, untrusted issue/comment/attachment metadata only:

```text
GET /links/session/{stored_session_id}/context?connection_id=local&profile_name=default&target_profile=default
```

## Development

Run the complete local quality gate from the repository root:

```bash
./scripts/test.sh
```

The script creates a disposable Python virtual environment, installs the hash-locked public
FastAPI/Pydantic test dependencies from `scripts/requirements-ci.txt`, supplies test-only compatibility
shims for the small Hermes APIs imported by this plugin, then runs JavaScript syntax checking,
Python `compileall`, full `unittest` discovery, and a repository secret/PII-pattern scan. It
uses disposable `HOME` and `HERMES_HOME` directories, clears all Jira configuration variables,
skips symlinks that resolve outside the repository, and never contacts Jira. The temporary
environment is removed when the script exits.

The suite covers Jira normalization and security, attachment handling, settings, worktree behavior, session links, API routes, and Desktop integration contracts.

## Repository layout

- `desktop/plugin.js` — Hermes Desktop route, board, drawers, sessions, settings UI, and self-contained live status integration.
- `desktop/data.js` — scoped issue batching, cache, offline snapshot, and mutation invalidation seam.
- `desktop/chat_context.js` — bounded composer context helpers for legacy Desktop builds.
- `dashboard/plugin_api.py` — namespaced FastAPI routes.
- `dashboard/jira_service.py` — Jira client, configuration, settings, Git worktrees, and link storage.
- `dashboard/manifest.json` — Dashboard plugin manifest.
- `tests/` — backend, API, Git, and Desktop contract tests.

## License

MIT — see [LICENSE](LICENSE).

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
- Supports explicit **Resume work**, **New chat**, chat attachment, and chat unlinking.
- Opens work sessions without submitting an agent task.
- Drafts Jira updates in a normal Hermes session; posting remains explicit.
- Suggests transitions but never applies one without a click.
- Stores human-editable, credential-free settings as JSON.

## Security model

- Jira credentials remain in the Python backend and are never written to Desktop storage, plugin settings, caches, prompts, or logs.
- Jira Basic Auth is allowed only over HTTPS.
- Authenticated redirects are refused so credentials cannot be forwarded to another host.
- Jira descriptions and comments are treated as untrusted data.
- Attachment metadata is validated against the requested issue before a bounded thumbnail is fetched.
- Worktree creation is noninteractive and disables repository hooks and injected global Git configuration.
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
5. Use **Scan chats** to discover related sessions. Exact linked-worktree matches attach automatically unless you explicitly unlinked that session.

## Settings

Settings are written atomically with mode `0600` to:

```text
$HERMES_HOME/jira-browser/settings.json
```

The drawer provides a form for common settings and a raw JSON editor for complete control. Settings include saved views, JQL, default view, page size, base ref, grouping, and UI preferences. Credentials are never stored there.

## Development

Run syntax checks and tests from the repository root in an environment where Hermes Agent is installed:

```bash
node --check desktop/plugin.js
python -m compileall -q dashboard tests
python -m unittest discover -s tests -v
```

The suite covers Jira normalization and security, attachment handling, settings, worktree behavior, session links, API routes, and Desktop integration contracts.

## Repository layout

- `desktop/plugin.js` — Hermes Desktop route, board, drawers, sessions, and settings UI.
- `dashboard/plugin_api.py` — namespaced FastAPI routes.
- `dashboard/jira_service.py` — Jira client, configuration, settings, Git worktrees, and link storage.
- `dashboard/manifest.json` — Dashboard plugin manifest.
- `tests/` — backend, API, Git, and Desktop contract tests.

## License

MIT — see [LICENSE](LICENSE).

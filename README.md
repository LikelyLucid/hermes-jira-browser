# Hermes Jira Browser

A native Jira workspace for Hermes Desktop. It presents Jira issues on a Board/List route, keeps issue details in a resizable drawer, and connects Jira tickets to real Hermes projects, worktrees, and sessions without using Kanban dispatch.

Project site: [Hermes Jira Browser](https://likelylucid.github.io/hermes-jira-browser/) · [Installation](#installation) · [Security](SECURITY.md)

## Features

- Defaults to the current Jira user's active assigned issues.
- Discovers Jira workflow lanes, including empty lanes, from available transitions.
- Supports saved JQL views, including a built-in **Bugs** viewer for all Jira issues whose type is `Bug`, pagination, stale-while-revalidate caches, and an attention view.
- Gives each saved view its own Board/List layout, sort order, and comfortable/compact density, with a friendly builder for common queues that do not require hand-written JQL.
- Makes saved views easier to curate with duplicate, move up/down, and remove controls while preserving the active/default view.
- Remembers the search text, quick filter, and attention-only state separately for each Jira view and owner scope.
- Provides quick filters for attention, blocked, unassigned, unlinked, working, and stale tickets; press `?` for the full keyboard-shortcut cheatsheet (navigation, copy actions, PR/JQL tools, layout toggles).
- Keeps cached tickets visible when refresh fails and labels live, cached, stale, offline, and error states with an explicit Retry action.
- Keeps list scanning useful with previous/next ticket navigation inside the detail drawer.
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
- Auto-detects the ticket's pull request from its local worktree branch and shows PR state, review decision, CI check rollup, branch ahead/behind, and dirty-file state in the ticket drawer, with a one-click **Open PR** action.
- Rolls up story points for the tickets currently shown, can sort any view by story points, copies a ticket key to the clipboard, and quietly refreshes a list older than two minutes when Desktop regains focus.
- Shows the worktree's changed files and recent commits in collapsible drawer sections, copies the worktree path or Jira link in one click, totals story points per board lane, and adds an **Unestimated** quick filter for tickets that still need a story-point value.
- Keeps the list column header sticky while scrolling, auto-loads the next page near the bottom, copies all visible ticket keys in one click, offers **Clear filters** when a narrowed list is empty, and collapses or expands every board lane at once.
- Remembers collapsed board lanes per saved view, adds a `c` shortcut (copy selected ticket key, or every visible key), scrolls the active row into view during j/k navigation, copies `KEY: summary` from the drawer, and shows the absolute timestamp behind every relative "updated" label.
- Adds `p`/`o` shortcuts (pin ticket beside chat, open in Jira), a copy-URL action beside **Open PR**, a "last loaded" timestamp tooltip on the freshness label, per-lane attention bells on the board, and a reason breakdown tooltip on the **Needs attention** toggle.
- Surfaces a cached `PR` badge on list rows and board cards (bounded, 30-minute TTL, refreshed whenever a ticket drawer opens), a `?` shortcuts cheatsheet, per-option counts in the quick-filter menu, subtask progress chips (`✓2/5`) when the project uses subtasks, and a background tick that keeps relative timestamps fresh.
- Persists comment drafts locally per ticket (7-day TTL, cleared on post, amber dot while unsent), submits comments with Ctrl/Cmd+Enter, adds a **Linked pull request** quick filter backed by the PR cache, lets Escape clear the text filter, and shows which story-point field auto-detection actually found in Settings.
- Adds a draft character counter that warns before the local limit, caches definitive "no pull request" checks so a **Pull request absent** filter can be trusted, shows the PR pill in the ticket drawer header and per-lane PR counts on the board, and lets you copy attachment filenames in one click.
- Surfaces every active narrowing (text filter, quick filter, attention) as one-click removable chips, wraps j/k navigation around the list, adds a **Discard** action for comment drafts, shows a header `N PR` count plus the ticket's attachment count in the drawer.
- Round 31: marks tickets with unsent comment drafts by a dot in list rows and board cards, gives the story-points rollup and visible-count chips full accessible names (the rollup also exposes `aria-pressed` for its sort state), shows both full URLs on hover when a manual PR link differs from the detected one, and adds attention bells with reason tooltips to the PR-link manager rows.
- Round 32: warms PR badges in the background for the first few visible uncached tickets (badges and the has-pr/no-pr filters fill in without opening each drawer), lets PR-link manager rows open their ticket by clicking the key and shows the draft dot there, marks the drawer header's selected key with the draft dot, and gives lane collapse/expand buttons `aria-expanded` state.
- Round 33: makes prewarmed PR data refresh has-pr/no-pr filter results immediately (cache-version dependency), prewarms attention tickets first and skips while offline, adds a one-click **Pin detected #N** action that saves the auto-detected pull request as the ticket's manual link, names lane story-point chips for screen readers, and shows how many linked tickets need attention in the settings-gear tooltip.
- Round 34: lets the PR-mismatch notice re-pin to the detected pull request in one click (polite live region, hidden on read-only connections), makes prewarmed badges refresh again after the 30-minute cache TTL instead of being skipped for the whole session, excludes the open ticket from the prewarm queue (its drawer already fetches), and gives the density toggle proper `switch`/`aria-checked` semantics.
- Round 35: detects manual links that have drifted from the auto-detected pull request inside the PR-link manager — per-row `≠ #N` re-pin chips, a **Re-pin N drifted** sweep, and drift counts in the panel label and gear tooltip — shares one attach-target helper between `⇧ a` and the prewarm queue (the target is always warmed even outside the top slice), and shows attention bells on import-preview rows.
- Round 36: fixes a crash in the Development section's PR-mismatch notice (its Re-pin handler referenced bindings that only existed in the ticket drawer, so a manual/detected mismatch could break the drawer render), adds a one-click pin icon directly on list/board PR badges for detected-but-unattached pull requests, makes row and sweep re-pins undoable (previous links stashed, one-shot **Undo re-pin**), counts attention keys in the import tally, warms 6 PR badges per pass, and gives the collapse-all lanes button expanded-state semantics.
- Round 37: stops the badge pin from appearing on already-attached tickets (a cached detection was shadowing the manual entry), makes the drawer's mismatch Re-pin undoable, adds `⇧ p` to pin the detected pull request for the current/top ticket from the keyboard, fetches detection data when pinning from the list so drift tracking has data, and keeps an open PR-link manager in sync with links changed elsewhere.
- Remembers the last-used saved view across launches, adds a `v` shortcut to toggle list/board (cheatsheet + hint included), shows an inline × to clear the filter, raises an error notification when a lane move fails, and announces result-count changes politely to screen readers.
- Makes the header chips interactive (pts chip toggles story-points sorting, `PR` chip toggles the pull-request filter, attachment chip jumps to the Attachments section), adds an `x` shortcut to collapse/expand all board lanes, and raises error notifications when drawer Jira writes (comment/transition) fail.
- Adds `Shift+j/k` to jump 10 rows and `n` to cycle tickets needing attention, shows how fresh each cached PR badge is, auto-retries a failed load when connectivity returns, and documents the Escape close order in the cheatsheet.
- Adds `Shift+n` (previous attention ticket), `Home`/`End` (first/last), `,` (settings), wraps drawer prev/next like j/k, and tells you when the empty state is due to filters rather than the view itself.
- Adds a one-click **JQL** copy button for the active view, auto-sizes the comment box to multi-line drafts, disables drawer prev/next when fewer than two tickets are shown (no self-loop), and documents the blur-first Escape order.
- Centralizes all shortcuts into one table feeding both the hint line and the cheatsheet (no more drift), adds `y` to copy the active JQL, a comments jump chip beside attachments, a Ctrl/Cmd+Enter hint on the comment box, and makes the toolbar row scroll instead of overflowing.
- Lets you **attach a pull-request URL to a ticket manually** (validated http(s), stored locally, Enter-to-attach, Open/Remove) when auto-detection is unavailable — attached PRs feed the same badge, header count, and has-pr/no-pr filters; keyboard navigation now scrolls board cards too, and local-storage reads/writes fail soft instead of throwing. The Development section renders your manual attachment when `gh` found no PR, warns when the manual link and the detected PR disagree, and the quick-filter menu says where its PR counts come from. Removing an attachment offers **Undo**, the attach field validates inline (URL keyboard, no spellcheck/autofill), and the active quick-filter chip shows its live count. Settings gains a **Manual PR links manager** (count, per-ticket removal, two-step **Remove all** with a summary toast, Copy JSON backup), the attach input collapses to a single toggle when `gh` already detected a PR, attaching (and restoring via Undo) confirms with a toast, the settings gear shows the live manual-link count, and lane PR chips disclose that counts cover gh-checked *and* manually attached links. Settings also **imports** the Copy-JSON export (per-entry key/URL validation, monospace preview rows of what will land, Enter-to-import, Escape-to-clear, skipped-count toast), `a` opens the top visible ticket and jumps straight to the attach field when the drawer is closed (focus follows Undo too, and Escape from either the field or Undo never leaks into the global Escape chain), and both the header PR chip and each lane's PR chip tell you when gh checks are over five minutes old.
- Fixes `⇧ j/k` and `⇧ n` (Shift produces uppercase keys, so the old lowercase matches never fired), adds `⇧ a` to attach on the first attention ticket that still needs a link (manual attachments and open gh PRs are skipped), makes letter shortcuts Caps Lock-proof, expands a collapsed board lane whenever a ticket opens so keyboard navigation reveals it, gives **Remove all** a session-persistent **Undo**, and badges manual-PR-link imports as new/update/identical with a full tally — identical entries are skipped on write so savedAt eviction order never shifts.
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
git clone https://github.com/LikelyLucid/hermes-jira-browser.git \
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

The drawer uses plain-language controls for common settings: choose a **Board** for moving active work through workflow columns or a **List** for scanning a backlog, choose the view to open by default, set how many tickets load at once, and choose the base ref for new worktrees. **My current sprint** (open-sprint tickets assigned to you) is the initial default; **All tickets** is a separate, recently updated list view rather than a quick filter within the sprint. The built-in **Bugs** view is a compact list using `issuetype = Bug`, and can be selected from the saved-view picker like any other view. Story points default to **auto**: Jira field metadata is inspected and the matching custom field is requested with issue searches. Set **Story points field** to `none` to hide the column, or enter an explicit id such as `customfield_10016` when your Jira instance uses a non-standard field. Missing, null, and malformed values are shown as an em dash rather than fabricated zeroes. Saved views are named Jira searches with independent layout, sort, and density preferences; the **Backlog** preset opens as a compact, priority-sorted list. Use **Create a saved view** to build common assigned/unassigned, open/completed, blocked/review, and label-based queues without writing JQL. Duplicate a view to keep its query and presentation as a starting point, or move it up/down to organize the picker. On the ticket surface, quick filters narrow the current result set without changing the saved query, and each view remembers its search/filter state in owner-scoped Desktop storage. Failed refreshes keep bounded cached results visible and expose a stale/offline/error label plus **Retry**. The keyboard hint shows `/` to filter, `j`/`k` to move, `b`/`l` to switch layout, `r` to refresh, and `Escape` to close. Advanced users can expand the credential-free JSON editor, but most users never need it. Credentials are never stored there.

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

## Keyboard shortcuts

The same table the in-app `?` cheatsheet shows (kept in sync by contract test; letter shortcuts are Caps Lock-proof and the `⇧` rows need a real Shift press):

| Keys | Action |
| --- | --- |
| `/` | Focus filter |
| `j / k` | Next / previous ticket |
| `⇧ j / ⇧ k` | Jump 10 rows |
| `Home / End` | First / last ticket |
| `n` | Next ticket needing attention |
| `⇧ n` | Previous attention ticket |
| `b / l` | Board / list layout |
| `r` | Refresh tickets |
| `c` | Copy ticket key(s) |
| `p` | Pin beside chat |
| `⇧ p` | Pin detected PR for this ticket |
| `o` | Open in Jira |
| `a` | Attach PR link · top ticket when closed |
| `⇧ a` | Attach PR · attention ticket needing a link |
| `y` | Copy active JQL |
| `v` | Toggle list/board |
| `x` | Collapse/expand lanes |
| `,` | Open settings |
| `?` | Toggle this help |
| `Esc` | Blur → import/disarm → help → panels → filter |

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

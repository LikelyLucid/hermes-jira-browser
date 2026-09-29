from pathlib import Path
import re
import subprocess
import unittest


PLUGIN = Path(__file__).parents[1] / "desktop" / "plugin.js"


class DesktopPluginTests(unittest.TestCase):
    def test_sprint_is_the_initial_view_and_all_tickets_is_a_saved_view_not_quick_filter(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("const DEFAULT_JQL = 'sprint in openSprints() AND assignee = currentUser() ORDER BY updated DESC'", source, "default query")
        self.assertIn("label: 'My current sprint'", source, "label must disclose assigned-only scope")
        self.assertIn("const LAST_ACTIVE_VIEW_KEY = 'last-active-view-v2'", source, "old assigned selection must not mask new default")
        self.assertIn("useState('current-sprint')", source, "initial view")
        self.assertIn("label: 'All in view'", source, "quick filter should not imply all Jira tickets")
        self.assertIn("readLastActiveViewId()", source, "future explicit view selection should still be remembered")

    def test_older_backend_settings_have_a_sprint_and_all_view_without_readding_user_deleted_v2_views(self):
        source = PLUGIN.read_text(encoding="utf-8")
        constants = source[source.index("const DEFAULT_JQL =") : source.index("\nconst ISSUE_CACHE_KEY =")]
        helper = source[source.index("function withSprintViews(") : source.index("\nconst LAST_ACTIVE_VIEW_KEY")]
        self.assertIn("withSprintViews(settingsResult?.settings)", source)
        probe = f"""
          const assert = require('node:assert/strict');
          {constants}
          {helper}
          const assigned = {{ id: 'assigned', jql: 'assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC' }};
          const custom = {{ id: 'custom', jql: 'project = DEMO' }};
          const legacy = {{ version: 1, defaultView: 'assigned', views: [assigned, custom] }};
          const migrated = withSprintViews(legacy);
          assert.equal(migrated.defaultView, 'current-sprint');
          assert.deepEqual(migrated.views.map(view => view.id), ['current-sprint', 'all', 'assigned', 'custom']);
          assert.equal(migrated.views[0].jql, DEFAULT_JQL);
          assert.strictEqual(withSprintViews(migrated), migrated);
          const removedAll = {{ version: 1, defaultView: 'current-sprint', views: [SPRINT_VIEW, assigned] }};
          assert.strictEqual(withSprintViews(removedAll), removedAll);
          assert.equal(withSprintViews({{ ...legacy, defaultView: 'custom' }}).defaultView, 'custom');
          assert.strictEqual(withSprintViews({{ version: 2, defaultView: 'assigned', views: [assigned] }}).views.length, 1);
          const oldSprint = {{ ...SPRINT_VIEW, label: 'Current sprint', jql: 'sprint in openSprints() ORDER BY updated DESC' }};
          const oldSaved = {{ version: 2, defaultView: 'current-sprint', views: [oldSprint, ALL_VIEW, custom] }};
          const upgraded = withSprintViews(oldSaved);
          assert.equal(upgraded.views[0].jql, DEFAULT_JQL);
          assert.equal(upgraded.views[0].label, 'My current sprint');
          assert.equal(upgraded.views[2].jql, 'project = DEMO');
          assert.strictEqual(withSprintViews(upgraded), upgraded);
          assert.equal(withSprintViews({{ ...oldSaved, version: 1 }}).views[0].jql, DEFAULT_JQL);
          const customSprint = {{ ...oldSprint, jql: 'project = DEMO AND sprint in openSprints()' }};
          assert.strictEqual(withSprintViews({{ ...oldSaved, views: [customSprint, ALL_VIEW] }}).views[0].jql, customSprint.jql);
          assert.strictEqual(withSprintViews({{ ...oldSaved, version: 3 }}).views[0].jql, oldSprint.jql);
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_status_colors_use_native_hermes_theme_tokens(self):
        source = PLUGIN.read_text(encoding="utf-8")

        status_color = source[source.index("function statusColor") : source.index("function normaliseProjects")]
        self.assertIn("var(--ui-accent)", status_color)
        self.assertNotIn("#60a5fa", status_color)

    def test_ticket_lists_use_persistent_stale_while_revalidate_cache(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const ISSUE_CACHE_KEY = 'issue-list-cache-v1'", source)
        self.assertIn("pluginContext.storage.get(ISSUE_CACHE_KEY", source)
        self.assertIn("pluginContext.storage.set(ISSUE_CACHE_KEY", source)
        self.assertIn("function cacheScopeKey", source)
        self.assertIn("resolvedOwner?.targetProfile", source)
        self.assertIn("readWorkStateCache(cacheOrigin, cacheOwner)", source)
        self.assertIn("readTicketWorktree(issue?.key, cacheOrigin)", source)
        self.assertIn("Cached · last updated", source)

    def test_cached_issue_rows_render_with_links_and_lanes_in_the_same_update(self):
        source = PLUGIN.read_text(encoding="utf-8")
        load = source[source.index("  const loadIssues = useCallback") : source.index("  // Effect dependency arrays")]
        self.assertIn("function cachedViewState(", source)
        self.assertLess(load.index("primeCachedView(cached.issues"), load.index("setIssues(current => reuseUnchangedIssues(current, cached.issues))"))
        self.assertLess(load.index("primeCachedView(rows"), load.index("setIssues(current => reuseUnchangedIssues(current, rows))"))
        self.assertIn("loading: !Array.isArray(previous.links)", source)
        helper = source[source.index("function readLaneCache(") : source.index("\nfunction readTicketWorktree(")]
        # Exercise the cache readers against an owner-scoped storage stub; the
        # first paint must already have both linked work and workflow lanes.
        probe = f"""
          const assert = require('node:assert/strict');
          const cacheScopeKey = (origin, owner) => `${{origin}}:${{owner}}`;
          const laneCacheId = (projects, scope) => `${{scope}}:${{projects.join('|')}}`;
          const mergeLaneDefinitions = (...groups) => groups.flat();
          const WORK_STATE_CACHE_KEY = 'work';
          const LANE_CACHE_KEY = 'lanes';
          const pluginContext = {{ storage: {{ get(key) {{
            if (key === WORK_STATE_CACHE_KEY) return {{ 'jira:user': {{ states: {{ 'DEMO-1': {{ links: [{{ session_id: 'work-1' }}] }} }} }} }};
            if (key === LANE_CACHE_KEY) return {{ 'jira:user:DEMO': {{ lanes: [{{ label: 'Ready', category: 'new' }}] }} }};
            return {{}};
          }} }} }};
          {helper}
          const rows = [{{ key: 'DEMO-1', project_key: 'DEMO', status: 'In Progress', status_category: 'indeterminate' }}];
          const hydrated = cachedViewState(rows, 'jira', 'user');
          assert.equal(hydrated.workStates['DEMO-1'].links[0].session_id, 'work-1');
          assert.deepEqual(hydrated.lanes.map(lane => lane.label), ['Ready', 'In Progress']);
          assert.deepEqual(cachedViewState(rows, 'other', 'user').workStates, {{}});
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_cached_work_states_preserve_newer_live_values_and_scope(self):
        source = PLUGIN.read_text(encoding="utf-8")
        helper = source[source.index("function reuseCachedWorkStates(") : source.index("\nfunction cachedViewState(")]
        self.assertIn("scopeChanged ? snapshot.workStates : reuseCachedWorkStates(current, snapshot.workStates)", source)
        probe = f"""
          const assert = require('node:assert/strict');
          {helper}
          const current = {{ 'DEMO-1': {{ links: [1], storedAt: 200 }} }};
          assert.strictEqual(reuseCachedWorkStates(current, {{ 'DEMO-1': {{ links: [2], storedAt: 100 }} }}), current);
          assert.deepEqual(reuseCachedWorkStates(current, {{ 'DEMO-1': {{ links: [2], storedAt: 300 }} }})['DEMO-1'].links, [2]);
          assert.deepEqual(Object.keys(reuseCachedWorkStates({{}}, {{ 'DEMO-1': current['DEMO-1'] }})), ['DEMO-1']);
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_first_uncached_view_waits_for_primary_enrichment_not_stale_rows(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("coldView || (loading && issues.length === 0)", source)
        self.assertIn("setColdView(Boolean(!cached || cached.issues.some(issue => !Array.isArray(cachedSnapshot.workStates[issue.key]?.links)) || !cachedSnapshot.laneReady))", source)
        helper = source[source.index("function viewEnrichmentReady(") : source.index("\nfunction readTicketWorktree(")]
        probe = f"""
          const assert = require('node:assert/strict');
          {helper}
          const issues = [{{ key: 'DEMO-1' }}];
          assert.equal(viewEnrichmentReady(issues, {{}}, true), false);
          assert.equal(viewEnrichmentReady(issues, {{ 'DEMO-1': {{ loading: true }} }}, true), false);
          assert.equal(viewEnrichmentReady(issues, {{ 'DEMO-1': {{ loading: false }} }}, true), false);
          assert.equal(viewEnrichmentReady(issues, {{ 'DEMO-1': {{ loading: false, links: [] }} }}, false), false);
          assert.equal(viewEnrichmentReady(issues, {{ 'DEMO-1': {{ loading: false, links: [] }} }}, true), true);
          assert.equal(viewEnrichmentReady(issues, {{ 'DEMO-1': {{ loading: false, refreshFailed: true }} }}, true), true);
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_unchanged_cached_lanes_do_not_trigger_another_render(self):
        source = PLUGIN.read_text(encoding="utf-8")
        helper = source[source.index("function reuseUnchangedLanes(") : source.index("\nfunction issueAttentionReasons(")]
        self.assertIn("setDetectedLanes(current => reuseUnchangedLanes(current, snapshot.lanes))", source)
        self.assertIn("setDetectedLanes(current => reuseUnchangedLanes(current, mergeLaneDefinitions(cached, issueStatuses)))", source)
        probe = f"""
          const assert = require('node:assert/strict');
          {helper}
          const old = [{{ key: 'new:Ready', label: 'Ready', category: 'new', rank: 0 }}];
          assert.strictEqual(reuseUnchangedLanes(old, [{{ ...old[0] }}]), old);
          assert.notStrictEqual(reuseUnchangedLanes(old, [{{ ...old[0], label: 'Done' }}]), old);
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_identical_live_issue_refresh_reuses_rendered_rows(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("setIssues(current => reuseUnchangedIssues(current, rows))", source)
        helper = source[source.index("function reuseUnchangedIssues(") : source.index("\nfunction laneCacheId(")]
        probe = f"""
          const assert = require('node:assert/strict');
          {helper}
          const old = [{{ key: 'DEMO-1', summary: 'First', labels: ['one'] }}];
          assert.strictEqual(reuseUnchangedIssues(old, [{{ key: 'DEMO-1', summary: 'First', labels: ['one'] }}]), old);
          assert.deepEqual(reuseUnchangedIssues(old, [{{ key: 'DEMO-1', summary: 'New' }}])[0].summary, 'New');
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_status_lanes_match_the_native_kanban_board_shape(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("flex h-full w-64 shrink-0 flex-col rounded-lg p-2", source)
        self.assertIn("flex h-full w-8 shrink-0 flex-col items-center", source)
        self.assertIn("[writing-mode:vertical-rl]", source)
        self.assertIn("overflow-x-auto", source)
        self.assertIn("draggable: true", source)
        self.assertIn("const moveIssueToLane = useCallback", source)
        self.assertIn("/transitions`, {", source)

    def test_ticket_opens_in_a_native_kanban_style_drawer(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("absolute inset-y-0 right-0 z-20 flex", source)
        self.assertIn("slide-in-from-right-4", source)
        self.assertIn("onClick: () => setSelectedKey('')", source)

    def test_ticket_detail_failure_has_scoped_retry_without_stale_context(self):
        source = PLUGIN.read_text(encoding="utf-8")
        load = source[source.index("  useEffect(() => {\n    setDetail(null)\n    setMapping(null)") : source.index("  const laneProbeKeys = useMemo")]
        drawer = source[source.index("'aria-label': 'Resize ticket details'") : source.index("export default {")]

        self.assertIn("setDetail(null)", load)
        self.assertIn("setMapping(null)", load)
        self.assertIn("setLinks([])", load)
        self.assertIn("setDetailError(null)", load)
        self.assertIn("setDetailError({ key: selectedKey, message: errorText(cause, `Could not load ${selectedKey}.`) })", load)
        self.assertNotIn("setError(errorText(cause, `Could not load ${selectedKey}.`))", load)
        self.assertIn("[selectedKey, status?.base_url, detailRetry]", load)
        self.assertIn("role: 'alert'", drawer)
        self.assertIn("detailError?.key === selectedKey", drawer)
        self.assertIn("children: detailError.message", drawer)
        self.assertIn("onClick: retryDetail", drawer)
        self.assertIn("detail?.key === selectedKey", drawer)

    def test_ticket_title_uses_the_full_drawer_row(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn(
            "className: 'break-words text-base font-semibold leading-snug text-foreground'",
            source,
        )

    def test_ticket_detail_leads_with_title_work_and_description(self):
        source = PLUGIN.read_text(encoding="utf-8")
        detail = source[source.index("function IssueDetail(") : source.index("function FriendlyViewBuilder(")]
        content = detail[detail.index("  return jsxs('div', {\n    className: 'space-y-5'") :]

        self.assertLess(content.index("children: issue.summary"), content.index("children: busyAction === 'resume'"))
        self.assertLess(content.index("children: busyAction === 'work'"), content.index("'aria-label': 'Open in Jira'"))
        self.assertLess(content.index("children: 'Description'"), content.index("children: 'Pull request'"))
        self.assertIn("children: 'More ticket details'", content)
        self.assertIn("{ label: 'Story points', value: storyPointsText(issue) }", content)

    def test_ticket_renders_jira_attachments_and_comment_screenshots(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function JiraAttachment({ attachment, issueKey })", source)
        self.assertIn("/attachments/${encodeURIComponent(attachment.id)}/preview", source)
        self.assertIn("loading: 'lazy'", source)
        self.assertIn("issue.attachments", source)
        self.assertIn("comment.attachments", source)
        self.assertIn("Attachments · ${unplacedAttachments.length}", source)
        self.assertIn("const ATTACHMENT_PREVIEW_CACHE_LIMIT = 6", source)
        self.assertIn("attachmentPreviewCache.size > ATTACHMENT_PREVIEW_CACHE_LIMIT", source)
        self.assertIn("attachmentPreviewCache.keys().next().value", source)

    def test_ticket_discloses_when_jira_comment_safety_cap_is_reached(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("issue.comments_truncated", source)
        self.assertIn("Only the first ${issue.comments.length} Jira comments are shown.", source)

    def test_ticket_drawer_is_pointer_and_keyboard_resizable(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const DRAWER_WIDTH_KEY = 'ticket-drawer-width-v1'", source)
        self.assertIn("const [drawerWidth, setDrawerWidth]", source)
        self.assertIn("role: 'separator'", source)
        self.assertIn("onPointerDown: beginDrawerResize", source)
        self.assertIn("onPointerMove: resizeDrawer", source)
        self.assertIn("onKeyDown: resizeDrawerWithKeyboard", source)
        self.assertIn("style: { width: `${drawerWidth}px` }", source)
        self.assertIn("writeDrawerWidth", source)

    def test_settings_use_the_side_drawer_with_human_and_agent_editors(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function SettingsDrawer", source)
        self.assertIn("children: 'Display'", source)
        self.assertIn("Ticket layout", source)
        self.assertIn("More display options", source)
        self.assertIn("Advanced settings", source)
        self.assertIn("$HERMES_HOME/jira-browser/settings.json", source)
        self.assertIn("const updateSettingsField = useCallback", source)
        self.assertIn("const updateSavedView = useCallback", source)
        self.assertIn("onAddBacklogView: addBacklogView", source)
        self.assertIn("onCopyPath: copySettingsPath", source)
        self.assertIn("style: { width: `${drawerWidth}px` }", source)

    def test_settings_lead_with_display_and_keep_rare_options_collapsed(self):
        source = PLUGIN.read_text(encoding="utf-8")
        settings = source[source.index("function SettingsDrawer(") : source.index("function JiraPage(")]
        render = settings[settings.index("  return jsxs('div', {\n    className: 'space-y-5'") :]

        self.assertIn("const manualPrLinks = jsxs('details'", settings)
        self.assertLess(render.index("children: 'Display'"), render.index("children: `Saved views · ${views.length}`"))
        self.assertLess(render.index("children: `Saved views · ${views.length}`"), render.index("manualPrLinks,"))
        self.assertLess(render.index("manualPrLinks,"), render.index("children: 'Advanced settings'"))
        self.assertIn("children: 'More display options'", render)
        self.assertIn("children: 'Advanced · ID and JQL'", render)
        self.assertIn("title: 'Identifier used in links and saved view state'", render)
        self.assertIn("jsxs('details', {\n              className: 'rounded-md border", render)
        self.assertIn("children: 'No manual links. Attach a pull request from a ticket.'", settings)

    def test_settings_save_failure_is_visible_and_retries_the_draft(self):
        source = PLUGIN.read_text(encoding="utf-8")
        header = source[source.index("'aria-label': 'Resize Jira settings'") : source.index("children: jsx(SettingsDrawer, {")]
        save = source[source.index("if (!settingsDraft || settingsDraft === lastSavedSettings.current) return") : source.index("const mutateSettingsDraft = useCallback")]

        self.assertIn("role: 'alert'", header)
        self.assertIn("children: settingsError", header)
        self.assertIn("settingsState === 'Save failed'", header)
        self.assertIn("onClick: settingsState === 'Save failed' ? retrySettingsSave : reloadSettingsFile", header)
        self.assertIn("setSettingsRetry", source)
        self.assertIn("settingsRetry])", save)
        self.assertIn("setSettingsState('Save failed')", save)
        self.assertIn("setSettingsState('Invalid JSON')", save)

    def test_secondary_ticket_actions_are_compact_and_named(self):
        source = PLUGIN.read_text(encoding="utf-8")
        detail = source[source.index("function IssueDetail(") : source.index("function FriendlyViewBuilder(")]
        for label, tooltip in (
            ('Open in Jira', 'Open this ticket in Jira'),
            ('Copy link', 'Copy ticket URL'),
            ('Copy summary', 'Copy ticket key and summary'),
            ('Pin beside chat', 'Pin this ticket beside the chat'),
        ):
            self.assertIn(f"'aria-label': '{label}'", detail)
            self.assertIn(f"title: '{tooltip}'", detail)
        self.assertIn("'aria-label': busyAction === 'link' ? 'Linking current chat' : 'Link current chat'", detail)
        self.assertIn("title: 'Link the current chat to this ticket'", detail)
        self.assertIn("'aria-label': busyAction === 'draft-update' ? 'Drafting update' : 'Draft update'", detail)
        self.assertIn("title: 'Draft a Jira update in a chat; nothing is posted to Jira'", detail)
        self.assertIn("'aria-label': busyAction === 'current-worktree' ? 'Linking current worktree' : 'Use current worktree'", detail)
        self.assertIn("'aria-label': 'Unlink worktree'", detail)
        self.assertIn("size: 'icon-xs'", detail)
        self.assertIn("!availableWorktrees.some(worktree => worktree.path === linkedWorktree.path)", detail)

    def test_board_cards_remain_keyboard_accessible_with_compact_statuses(self):
        source = PLUGIN.read_text(encoding="utf-8")
        card = source[source.index("function JiraCard(") : source.index("function JiraListRow(")]

        self.assertIn("role: 'button'", card)
        self.assertIn("tabIndex: 0", card)
        self.assertIn("if (event.key !== 'Enter' && event.key !== ' ') return", card)
        self.assertIn("focus-visible:ring-(--dt-composer-ring)", card)
        self.assertIn("${issue.priority} priority", card)
        self.assertIn("assigned to ${issue.assignee}", card)
        self.assertIn("title: attentionReasons.join(' · ')", card)
        self.assertIn("'aria-label': `Priority: ${issue.priority}`", card)
        self.assertIn("'aria-label': `Assignee: ${issue.assignee}`", card)

    def test_bug_view_and_story_points_are_first_class_ticket_features(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("hasStoryPointsField", source)
        self.assertIn("storyPointsField", source)
        self.assertIn("Story points", source)
        self.assertIn("story_points_field", source)
        self.assertIn("showStoryPoints", source)
        self.assertIn("children: 'Points'", source)

    def test_pull_request_auto_detection_is_surfaced_in_ticket_detail(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("/repository-context", source)
        self.assertIn("IssueDevelopmentSection", source)
        self.assertIn("prCheckRollup", source)
        self.assertIn("shownPullRequest.review_decision", source)
        self.assertIn("'Open PR'", source)
        self.assertIn("githubReasonText", source)
        self.assertIn("copyIssueKey", source)
        self.assertIn("writeClipboard", source)

    def test_points_rollup_sort_and_focus_refresh_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("{ value: 'points', label: 'Story points' }", source)
        self.assertIn("if (sort === 'points')", source)
        self.assertIn("storyPointsRollup", source)
        self.assertIn("children: `${storyPointsRollup} pts`", source)
        self.assertIn("FOCUS_REFRESH_MIN_MS", source)
        self.assertIn("visibilitychange", source)
        self.assertIn("lastLoadedAtRef", source)

    def test_development_section_shows_files_commits_and_lane_points(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("changedFileStatusLabel", source)
        self.assertIn("`Changed files · ${changedFiles}", source)
        self.assertIn("`Recent commits · ${recentCommits.length}", source)
        self.assertIn("recentCommits.slice(0, 5)", source)
        self.assertIn("lanePoints", source)
        self.assertIn("'Story points in this lane'", source)
        self.assertIn("{ value: 'unestimated', label: 'Unestimated' }", source)
        self.assertIn("filter === 'unestimated'", source)
        self.assertIn("copyTextToClipboard", source)
        self.assertIn("'aria-label': 'Copy link'", source)
        self.assertIn("'aria-label': 'Copy worktree path'", source)

    def test_list_scanning_and_batch_actions_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Sticky column header keeps long bug lists scannable.
        self.assertIn("sticky top-0 z-10 bg-(--ui-surface-background)", source)
        # Auto-load pagination keeps a sentinel observation and the manual button.
        self.assertIn("ref: listEndRef", source)
        self.assertIn("IntersectionObserver", source)
        self.assertIn("children: loadingMore ? 'Loading…' : 'Load more tickets'", source)
        # Empty state offers a one-click way back out of narrowing filters.
        self.assertIn("hasActiveNarrowing", source)
        self.assertIn("clearNarrowingFilters", source)
        self.assertIn("children: 'Clear filters'", source)
        # Board lanes can be collapsed or expanded in one action.
        self.assertIn("toggleAllLanes", source)
        self.assertIn("'aria-label': allLanesCollapsed ? 'Expand all lanes' : 'Collapse all lanes'", source)
        # One click copies every visible ticket key for standups or PR bodies.
        self.assertIn("'aria-label': 'Copy visible ticket keys'", source)
        self.assertIn("keys.join('\\n')", source)

    def test_navigation_and_persisted_view_state_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Collapsed board lanes persist per saved view across reloads.
        self.assertIn("collapsedLanes: normaliseCollapsedLaneKeys(state.collapsedLanes)", source)
        self.assertIn("normaliseCollapsedLaneKeys(saved?.collapsedLanes)", source)
        self.assertIn("collapsedLaneKeys", source)
        # The `c` shortcut copies the selected key, or all visible keys otherwise.
        self.assertIn("if (event.key === 'c')", source)
        self.assertIn("children: `Keyboard shortcuts: ${SHORTCUT_HINT}`", source)
        # Keyboard navigation scrolls the active list row into view.
        self.assertIn("'data-jira-row': issue.key", source)
        self.assertIn("scrollIntoView?.({ block: 'nearest' })", source)
        # Drawer can copy "KEY: summary" for pasting into notes or PR bodies.
        self.assertIn("copyIssueSummary", source)
        self.assertIn("'aria-label': 'Copy summary'", source)
        # Relative timestamps carry the absolute date in their tooltip.
        self.assertIn("function absoluteDate(", source)
        self.assertIn("title: absoluteDate(issue.updated)", source)

    def test_attention_and_pull_request_actions_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # `p` pins the selected ticket (ref-indirection avoids declaration order issues),
        # `o` opens the selected ticket in Jira.
        self.assertIn("pinTicketRef.current?.()", source)
        self.assertIn("pinTicketRef.current = pinTicket", source)
        self.assertIn("if (event.key === 'p')", source)
        self.assertIn("if (event.key === 'o')", source)
        self.assertIn("const openSelectedInJira = useCallback", source)
        self.assertIn("['r', 'Refresh tickets']", source)
        # The pull request row can copy its URL alongside the Open PR action.
        self.assertIn("'aria-label': 'Copy pull request URL'", source)
        # Freshness label exposes when the list last loaded.
        self.assertIn("Last loaded ${absoluteDate(lastLoadedAtRef.current)}", source)
        # Board lanes show an amber attention bell with a per-lane count.
        self.assertIn("laneAttention", source)
        self.assertIn("need attention in this lane", source)
        # The Needs attention toggle explains its breakdown in a tooltip.
        self.assertIn("attentionBreakdown", source)
        self.assertIn("title: attentionBreakdown ||", source)

    def test_pull_request_badges_and_shortcuts_help_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Cached PR status badges (bounded, 30-minute TTL) on list rows and board cards.
        self.assertIn("PR_STATUS_CACHE_KEY", source)
        self.assertIn("PR_STATUS_MAX_AGE_MS", source)
        self.assertIn("function PrStatusBadge(", source)
        self.assertEqual(source.count("jsx(PrStatusBadge, { entry: prEntry, issueKey: issue.key })"), 2)
        self.assertIn("prEntry: prStatusByKey?.[issue.key]", source)
        self.assertIn("function subscribePrStatus(", source)
        # Shortcuts cheatsheet toggled by `?` or the clickable hint.
        self.assertIn("const [showShortcuts, setShowShortcuts]", source)
        self.assertIn("if (event.key === '?')", source)
        self.assertIn("'Toggle this help'", source)
        self.assertIn("setShowShortcuts(false)", source)
        # Quick-filter options show how many loaded tickets match each option.
        self.assertIn("const count = quickFilterCounts[option.value] || 0", source)
        # Subtask progress chips degrade cleanly when the field is absent.
        self.assertIn("`✓${subtaskCompleted}/${subtasks.length}`", source)
        self.assertIn("`Subtasks: ${subtaskCompleted} of ${subtasks.length} completed`", source)
        # The minute ticker keeps relative timestamps fresh without a reload.
        self.assertIn("setClockTick", source)
        self.assertIn("60_000", source)

    def test_comment_drafts_and_detection_hints_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Comment drafts persist per ticket (bounded, 7-day TTL) and clear on post.
        self.assertIn("COMMENT_DRAFT_CACHE_KEY", source)
        self.assertIn("COMMENT_DRAFT_MAX_AGE_MS", source)
        self.assertIn("function readCommentDraft(", source)
        self.assertIn("function writeCommentDraft(", source)
        self.assertIn("setCommentDraft(readCommentDraft(cacheScope, issue?.key))", source)
        self.assertIn("writeCommentDraft(cacheScope, issue.key, '')", source)
        # Amber dot marks an unsaved draft; Ctrl/Cmd+Enter submits it.
        self.assertIn("Draft saved locally on this machine", source)
        self.assertIn("(event.metaKey || event.ctrlKey) && event.key === 'Enter'", source)
        # `has-pr` quick filter reads the PR status cache.
        self.assertIn("{ value: 'has-pr', label: 'Linked pull request', hint: 'gh-checked or attached manually' }", source)
        self.assertIn("filter === 'has-pr'", source)
        # Escape clears the text filter when no other panel is open.
        self.assertIn("} else if (String(filter || '').trim()) {", source)
        # Settings show what auto-detection actually found in loaded tickets.
        self.assertIn("Detected: ${detectedStoryPoints}", source)
        self.assertIn("const detectedStoryPoints = useMemo(", source)

    def test_pr_cache_negative_results_and_attachment_copies_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Draft length counter warns before the local slice limit.
        self.assertIn("`${commentDraft.length} / ${COMMENT_DRAFT_TEXT_LIMIT}`", source)
        # Definitive no-PR checks are cached as negatives; the badge skips them.
        self.assertIn(": { pr: false, fetchedAt: Date.now() })", source)
        self.assertIn("if (!entry || entry.pr === false) return null", source)
        self.assertIn("{ value: 'no-pr', label: 'Pull request absent', hint: 'gh-verified with no recent PR' }", source)
        self.assertIn("filter === 'no-pr'", source)
        # The ticket drawer header shows the cached PR pill immediately.
        self.assertIn("jsx(PrStatusBadge, { entry: prStatusByKey?.[selectedKey] })", source)
        # Board lanes count tickets with a linked pull request.
        self.assertIn("const lanePrCount = lane.issues.filter(", source)
        self.assertIn("with a linked pull request", source)
        # Both attachment row shapes can copy their filename.
        self.assertEqual(source.count("'aria-label': 'Copy attachment filename'"), 2)

    def test_narrowing_chips_and_keyboard_wrap_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Active narrowing shows removable chips in the toolbar.
        self.assertIn("function NarrowingChip(", source)
        self.assertIn("hasActiveNarrowing", source)
        self.assertIn("label: `Filter: ${filter.trim()}`", source)
        self.assertIn("onClear: () => setQuickFilter('all')", source)
        self.assertIn("label: `Needs attention · ${attentionCount}`, onClear: () => setAttentionOnly(false)", source)
        # j/k navigation wraps around the visible list.
        self.assertIn("if (nextIndex < 0) nextIndex = total - 1", source)
        self.assertIn("else if (nextIndex >= total) nextIndex = 0", source)
        # Drafts can be discarded without posting.
        self.assertIn("'aria-label': 'Discard comment draft'", source)
        # Header counts tickets with a known pull request; drawer shows attachments.
        self.assertIn("const headerPrCount = useMemo(", source)
        self.assertIn("`${headerPrCount} PR`", source)
        self.assertIn("detail?.key === selectedKey && detail.attachments?.length", source)
        self.assertIn("children: [jsx(Codicon, { name: 'attach', size: '0.6rem' }), detail.attachments.length]", source)
        # headerPrCount must be declared after sortedVisibleIssues (no TDZ).
        self.assertGreater(
            source.find("const headerPrCount = useMemo("),
            source.find("const sortedVisibleIssues = useMemo("),
        )

    def test_view_memory_and_move_feedback_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The last used saved view is restored on next launch.
        self.assertIn("LAST_ACTIVE_VIEW_KEY", source)
        self.assertIn("function readLastActiveViewId(", source)
        self.assertIn("function writeLastActiveViewId(", source)
        self.assertIn("views.find(candidate => candidate.id === lastActiveViewId)", source)
        self.assertIn("writeLastActiveViewId(nextId)", source)
        # `v` toggles list/board (hint, cheatsheet, tooltip all mention it).
        self.assertIn("if (event.key === 'v')", source)
        self.assertIn("updateViewMode(effectiveListView === 'list' ? 'board' : 'list')", source)
        self.assertIn("['v', 'Toggle list/board']", source)
        self.assertIn("const SHORTCUT_HINT = SHORTCUT_ROWS.map(([keys]) => keys).join(' · ')", source)
        # Failed lane moves notify, not just the inline banner.
        self.assertIn("host.notify({ kind: 'error', message })", source)
        self.assertIn("`Could not move ${issueKey}.`", source)
        # Inline clear button on the filter input.
        self.assertIn("'aria-label': 'Clear filter'", source)
        self.assertIn("${filter ? 'pl-2 pr-6' : 'px-2'}", source)
        # The count chip announces politely for screen readers.
        self.assertIn("'aria-live': 'polite'", source)

    def test_chip_actions_and_write_failure_notices_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Drawer write failures raise a notification, not just an inline banner.
        self.assertIn("host.notify({ kind: 'error', message: errorText(cause, 'Could not add the Jira comment.", source)
        self.assertIn("host.notify({ kind: 'error', message: errorText(cause, 'Could not change the Jira status.", source)
        self.assertIn("host.notify({ kind: 'error', message: errorText(cause, 'Could not apply the suggested status.", source)
        # The pts chip toggles story-points sorting; the PR chip toggles has-pr.
        self.assertIn("onClick: () => updateActiveViewPreference('sort', activeViewPreferences.sort === 'points' ? 'updated' : 'points')", source)
        self.assertIn("onClick: () => setQuickFilter(quickFilter === 'has-pr' ? 'all' : 'has-pr')", source)
        # The attachment chip jumps to the attachments section.
        self.assertIn("id: 'jira-detail-attachments'", source)
        self.assertIn("getElementById('jira-detail-attachments')?.scrollIntoView", source)
        # `x` collapses/expands all board lanes (board-only, in the cheatsheet).
        self.assertIn("if (event.key === 'x')", source)
        self.assertIn("if (effectiveListView !== 'board') return", source)
        self.assertIn("['x', 'Collapse/expand lanes']", source)

    def test_triage_jumps_and_reconnect_retry_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Shift+j/k jumps 10 rows; `n` cycles tickets needing attention.
        self.assertIn("const step = event.shiftKey ? 10 : 1", source)
        self.assertIn("currentIndex + delta * step", source)
        self.assertIn("if (event.key === 'n' || event.key === 'N')", source)
        self.assertIn(": (at + 1) % needingAttention.length", source)
        self.assertIn("['n', 'Next ticket needing attention']", source)
        self.assertIn("['⇧ j / ⇧ k', 'Jump 10 rows']", source)
        # Escape order is documented in the cheatsheet.
        self.assertIn("['Esc', 'Blur → import/disarm → help → panels → filter']", source)
        # The PR badge reports how fresh its cached entry is.
        self.assertIn("checked ${checkedLabel}", source)
        self.assertIn("? 'just now'", source)
        # A failed load retries automatically when connectivity returns.
        self.assertIn("window.addEventListener('online', handleOnline)", source)
        self.assertIn("if (status?.configured && error && !loading) loadIssues(", source)

    def test_navigation_polish_and_filter_aware_empty_state_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Shift+n walks attention tickets backwards; Home/End jump to the ends.
        self.assertIn("(at <= 0 ? needingAttention.length - 1 : at - 1)", source)
        self.assertIn("event.key === 'Home' || event.key === 'End'", source)
        self.assertIn("['Home / End', 'First / last ticket']", source)
        # Drawer prev/next wrap like j/k.
        self.assertIn("(selectedIssueIndex - 1 + sortedVisibleIssues.length) % sortedVisibleIssues.length", source)
        self.assertIn("(selectedIssueIndex + 1) % sortedVisibleIssues.length", source)
        # `,` opens settings (same body as the toolbar gear) and is documented.
        self.assertIn("if (event.key === ',')", source)
        self.assertIn("[',', 'Open settings']", source)
        # The empty state distinguishes filtered-out from genuinely empty views.
        self.assertIn("hasActiveNarrowing ? 'No Jira tickets match the current filters.' : 'No Jira tickets match this view.'", source)

    def test_jql_copy_and_navigation_edge_polish_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The toolbar can copy the active view's JQL for pasting into Jira.
        self.assertIn("'aria-label': 'Copy active JQL'", source)
        self.assertIn("copyTextToClipboard(submittedJql, 'JQL')", source)
        # The comment textarea grows with pasted multi-line drafts.
        self.assertIn("rows: Math.min(12, Math.max(4, commentDraft.split('\\n').length))", source)
        # Prev/next disable instead of self-looping when fewer than 2 tickets show.
        self.assertIn("disabled: !previousIssueKey || sortedVisibleIssues.length < 2", source)
        self.assertIn("disabled: !nextIssueKey || sortedVisibleIssues.length < 2", source)
        # Titles advertise the wrap behavior; the hint line lists `n`.
        self.assertIn("'Previous ticket · wraps to the end'", source)
        self.assertIn("'Next ticket · wraps to the start'", source)

    def test_shortcut_table_source_of_truth_and_header_polish_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # One table feeds both the hint line and the cheatsheet.
        self.assertIn("const SHORTCUT_ROWS = [", source)
        self.assertIn("const SHORTCUT_HINT = SHORTCUT_ROWS.map(([keys]) => keys).join(' · ')", source)
        self.assertIn("...SHORTCUT_ROWS.map(([keys, label])", source)
        self.assertIn("['y', 'Copy active JQL']", source)
        # The comment box documents Ctrl/Cmd+Enter.
        self.assertIn("title: 'Ctrl/Cmd+Enter posts the comment'", source)
        # Comments chip mirrors the attachments jump chip.
        self.assertIn("id: 'jira-detail-comments'", source)
        self.assertIn("'aria-label': `Jump to ${detail.comments.length} comment", source)
        # The toolbar wraps its view controls and filters on narrow widths.
        toolbar = source[source.index("function JiraPage()") :]
        toolbar = toolbar[toolbar.index("      jsxs('header', {") : toolbar.index("      error\n")]
        self.assertIn("flex min-w-0 flex-wrap items-center gap-2", toolbar)
        self.assertNotIn("overflow-x-auto whitespace-nowrap", toolbar)
        # `y` copies the active JQL from the keyboard.
        self.assertIn("if (event.key === 'y')", source)
        self.assertIn("void copyTextToClipboard(submittedJql, 'JQL')", source)

    def test_shortcut_table_order_and_accessibility_polish_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The table's exact key order is pinned so row edits fail loudly in one place.
        block = source.split("const SHORTCUT_ROWS = [", 1)[1].split("\n]", 1)[0]
        self.assertEqual(
            re.findall(r"\['([^']+)'", block),
            ["/", "j / k", "⇧ j / ⇧ k", "Home / End", "n", "⇧ n", "b / l", "r", "c", "p", "⇧ p", "o", "a", "⇧ a", "y", "v", "x", ",", "?", "Esc"],
        )
        # The hint tooltip derives from the same table (last prose string retired).
        self.assertIn("const SHORTCUT_TITLE = `Keyboard shortcuts: ${SHORTCUT_ROWS.map(([keys, label]) => `${keys} ${label}`).join(' · ')}`", source)
        self.assertIn("title: SHORTCUT_TITLE,", source)
        # Custom chips/buttons expose a keyboard focus ring.
        self.assertIn("focus-visible:ring-1 focus-visible:ring-(--dt-composer-ring)", source)
        self.assertGreaterEqual(source.count("focus-visible:ring-1 focus-visible:ring-(--dt-composer-ring)"), 14)
        # The filter placeholder teaches the `/` shortcut.
        self.assertIn("placeholder: 'Filter tickets (press /)'", source)
        self.assertIn("['⇧ n', 'Previous attention ticket']", source)

    def test_manual_pr_attach_and_storage_hardening_are_quality_of_life(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Manual PR links: persistent, validated http(s), bounded, badge-integrated.
        self.assertIn("const PR_LINK_OVERRIDES_KEY = 'pull-request-link-overrides-v1'", source)
        self.assertIn("function prLinkOverrideValid(url)", source)
        self.assertIn("parsed.protocol !== 'https:' && parsed.protocol !== 'http:'", source)
        self.assertIn("state: 'manual', savedAt: Date.now()", source)
        self.assertIn("function writePrLinkOverride(issueKey, url)", source)
        # Overrides overlay the TTL cache (manual beats auto; write path stays raw).
        self.assertIn("const base = readPrStatusCacheRaw(force)", source)
        self.assertIn("return { ...overrideEntries, ...base }", source)
        self.assertIn("readPrStatusCacheRaw(true)", source)
        # Badge says 'attached manually' instead of a stale checked-age.
        self.assertIn("? `Pull request${entry.number ? ` #${entry.number}` : ''} · attached manually`", source)
        # Drawer section: attach input (Enter submits), Open/Remove once attached.
        self.assertIn("'aria-label': 'Attach pull request URL'", source)
        self.assertIn("placeholder: 'https://github.com/org/repo/pull/123'", source)
        self.assertIn("children: 'Attach'", source)
        self.assertIn("'aria-label': 'Open attached pull request'", source)
        self.assertIn("'aria-label': 'Remove attached pull request'", source)
        # Board cards carry the keyboard-scroll marker alongside list rows.
        self.assertEqual(source.count("'data-jira-row': issue.key"), 2)
        # Storage accessors fail soft (drafts/view memory never throw into UI).
        self.assertGreaterEqual(source.count("catch { /* optional storage */ }"), 5)
        self.assertIn("  } catch { return '' }", source)
        # Resize separators show a focus ring; the JQL button advertises `y`.
        self.assertIn("cursor-col-resize touch-none outline-none focus-visible:ring-1", source)
        self.assertIn("title: 'Copy the JQL for this view (y)'", source)

    def test_development_section_falls_back_to_manual_pr_with_mismatch_warning(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The Development card renders the manual attachment when gh found no PR.
        self.assertIn("function IssueDevelopmentSection({ context, manualPullRequest, issueKey = '', readOnly = false, onOverrideChanged = null })", source)
        self.assertIn("const shownPullRequest = pullRequest || manualPullRequestValid", source)
        self.assertIn("shownPullRequest.url", source)
        self.assertIn("(pullRequest", source)
        self.assertIn(": `Attached${shownPullRequest.number ? ` #${shownPullRequest.number}` : ''} pull request`)", source)
        self.assertNotIn("      pullRequest\n        ? jsxs('div'", source)
        # Detected vs manual disagreement is surfaced, not silently resolved.
        self.assertIn("const prMismatch = Boolean(pullRequest && manualPullRequestValid", source)
        self.assertIn("differs from the detected #${pullRequest.number}", source)
        # The drawer passes its attachment through; gh-absent hint is gated on the merged view.
        self.assertIn("jsx(IssueDevelopmentSection, {\n        context: repoContext,\n        manualPullRequest: prOverride,\n        issueKey: issue?.key,", source)
        self.assertIn("github && github.available === true && !shownPullRequest", source)
        # Filter menu explains its PR sources; the attach input is length-capped.
        self.assertIn("hint: 'gh-checked or attached manually'", source)
        self.assertIn("hint: 'gh-verified with no recent PR'", source)
        self.assertIn("title: option.hint", source)
        self.assertIn("(gh-checked or attached manually)", source)
        self.assertIn("maxLength: 500", source)

    def test_pr_attach_polish_undo_hint_and_input_hygiene(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Removing an attachment keeps the entry so the same ticket can Undo it.
        self.assertIn("const [removedPrLink, setRemovedPrLink] = useState(null)", source)
        self.assertIn("const entry = readPrLinkOverrides()[issue?.key] || null", source)
        self.assertIn("setRemovedPrLink(entry ? { key: issue?.key, entry } : null)", source)
        self.assertIn("const undoRemovePrLink = useCallback(() => {", source)
        self.assertIn("title: 'Restore the removed attachment'", source)
        self.assertIn("setRemovedPrLink(null)\n    setShowPrInput(false)", source)  # reset on ticket switch
        # Development card surfaces the PR URL on hover.
        self.assertIn("title: shownPullRequest.url || ''", source)
        # Attach input: no spellcheck noise, no autofill, URL keyboard.
        self.assertIn("spellCheck: false", source)
        self.assertIn("autoComplete: 'off'", source)
        self.assertIn("inputMode: 'url'", source)
        # Invalid-but-typed text explains itself instead of only a disabled button.
        self.assertIn("&& !prLinkOverrideValid(prLinkInput)", source)
        self.assertIn("children: 'Enter a full https://… pull-request URL.'", source)
        # The active quick-filter chip carries its live count like the menu.
        self.assertIn("?.label || quickFilter} · ${quickFilterCounts[quickFilter] || 0}", source)

    def test_pr_attach_settings_manager_and_detection_collapse(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Settings lists every manual attachment with count, copy-out, and removal.
        self.assertIn("const [prLinks, setPrLinks] = useState(() => readPrLinkOverrides())", source)
        self.assertIn("children: `Manual PR links · ${prLinkRows.length}${prDriftByKey.size > 0 ?", source)
        self.assertIn("JSON.stringify(Object.fromEntries(prLinkRows), null, 2), 'manual PR links'", source)
        self.assertIn("title: `Remove the ${key} pull request attachment`", source)
        self.assertIn("No manual links. Attach a pull request from a ticket.", source)
        # When gh already detected a PR, the attach input collapses to a ghost toggle.
        self.assertIn("const [showPrInput, setShowPrInput] = useState(false)", source)
        self.assertIn("const detectedPullRequest = Boolean(repoContext?.github?.pull_request", source)
        self.assertIn("children: 'Add manual link'", source)
        self.assertIn("? ' hidden' : ''}`", source)
        self.assertIn("setShowPrInput(false)\n  }, [issue?.key])", source)  # reset on ticket switch
        # Successful attach confirms with the parsed PR number.
        self.assertIn("host.notify({ kind: 'success', message: `Pull request ${label} attached to", source)
        # The attention narrowing chip mirrors the toolbar's live count.
        self.assertIn("label: `Needs attention · ${attentionCount}`", source)

    def test_pr_attach_bulk_removal_feedback_and_source_disclosure(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Bulk removal: one storage write, one subscriber flush, one summary toast,
        # and a two-step confirm so a stray click cannot wipe every attachment.
        self.assertIn("function clearPrLinkOverrides()", source)
        self.assertIn("pluginContext.storage.set(PR_LINK_OVERRIDES_KEY, {})", source)
        self.assertIn("children: confirmRemoveAll ? `Remove all · ${prLinkRows.length}?` : jsx(Codicon, { name: 'trash'", source)
        self.assertIn("if (!confirmRemoveAll) {", source)
        self.assertIn("const [confirmRemoveAll, setConfirmRemoveAll] = useState(false)", source)
        self.assertIn("if (!prLinkRows.length && confirmRemoveAll) setConfirmRemoveAll(false)", source)
        self.assertIn("message: `Removed ${removedCount} manual PR link${removedCount === 1 ? '' : 's'}.`", source)
        # Per-row removal in settings confirms which ticket it was.
        self.assertIn("message: `Manual PR link removed from ${key}.`", source)
        # Undo confirms the restore, mirroring the attach toast.
        self.assertIn("message: `Pull request attachment restored on ${issue?.key || 'this ticket'}.`", source)
        # Settings gear advertises how many manual links exist.
        self.assertIn("const manualPrLinkCount = Object.values(readPrLinkOverrides())", source)
        self.assertIn("`Jira settings · ${manualPrLinkCount} manual PR link${manualPrLinkCount === 1 ? '' : 's'}${prLinkAttentionCount > 0 ?", source)
        # Both lane chips (rail + header) disclose their PR-count sources.
        self.assertEqual(source.count("with a linked pull request (gh-checked or attached manually)"), 3)

    def test_pr_import_keyboard_access_and_freshness_disclosure(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Copy JSON's counterpart: paste-back import validates keys + URLs per entry.
        self.assertIn("function parsePrLinkImport(text)", source)
        self.assertIn("if (ISSUE_KEY_PATTERN.test(key) && url) entries.push([key, url])", source)
        self.assertIn("const [prImportText, setPrImportText] = useState('')", source)
        self.assertIn("disabled: !prImportParsed?.entries.length || importAllIdentical", source)
        self.assertIn(": 'Import'", source)
        self.assertIn("message: `Imported ${written} manual PR link${written === 1 ? '' : 's'}", source)
        self.assertIn("No valid pull request links found in that JSON.", source)
        # `a` expands + focuses the attach input; typing never triggers it.
        self.assertIn("['a', 'Attach PR link · top ticket when closed']", source)
        self.assertIn("if (!isAttachKey || typing) return", source)
        self.assertIn("document.addEventListener('keydown', handler, true)", source)
        self.assertIn("if (showPrInput) attachInputRef.current?.focus()", source)
        self.assertIn("ref: attachInputRef,", source)
        # Focus follows the keyboard: Undo receives focus when it appears.
        self.assertIn("if (removedPrLink) undoButtonRef.current?.focus()", source)
        self.assertIn("ref: undoButtonRef,", source)
        self.assertIn("jsx('button', {", source)  # Undo is a native host element so ref/focus works
        # Header PR chip discloses gh-check age when older than five minutes.
        self.assertIn("const newestGhPrCheckAt = prStatusByKey", source)
        self.assertIn("gh checks ${Math.round((Date.now() - newestGhPrCheckAt) / 60_000)} min old", source)
        self.assertIn("${prFreshnessNote} · click to filter", source)

    def test_attach_escape_import_preview_and_lane_freshness(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Escape collapses the attach field only when it holds focus, and is
        # swallowed there so the global blur → help → panels → filter chain
        # never fires on top of it.
        self.assertIn("if (event.key === 'Escape') {", source)
        self.assertIn("if (active === attachInputRef.current) {", source)
        self.assertIn("event.stopPropagation()", source)
        self.assertIn("setPrLinkInput('')\n          setShowPrInput(false)", source)
        # Discoverability: input documents Enter/Esc; the ghost hints `a`.
        self.assertIn("title: 'Enter attaches · Esc closes'", source)
        self.assertIn("even though one was detected · a", source)
        # Import previews what will land before the click (shared single parse).
        self.assertIn("const prImportParsed = parsePrLinkImport(prImportText)", source)
        self.assertIn("const parsed = prImportParsed", source)
        self.assertIn("...prImportParsed.entries.slice(0, 5).map(([key, url])", source)
        self.assertIn("const importInputRef = useRef(null)", source)
        self.assertIn("${prImportParsed.skipped ? `${prImportParsed.skipped} invalid skipped` : ''}", source)
        # Enter in the paste field imports when at least one entry parses.
        self.assertIn("if (event.key === 'Enter' && prImportParsed?.entries.length && !importAllIdentical)", source)
        # Lane chips carry their own per-lane gh freshness note.
        self.assertIn("const laneNewestGhCheckAt = Math.max(0, ...lane.issues", source)
        self.assertIn("const laneFreshnessNote = laneNewestGhCheckAt &&", source)
        self.assertEqual(source.count("(gh-checked or attached manually)${laneFreshnessNote}"), 1)

    def test_attach_from_list_undo_escape_and_import_field_escape(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # `a` with no ticket open opens the top visible ticket and queues the
        # attach field to expand on mount (consumed once, tied to that key).
        self.assertIn("const [attachRequest, setAttachRequest] = useState(null)", source)
        self.assertIn("if ((event.key === 'a' || (event.key === 'A' && !event.shiftKey)) && !selectedKey && !showSettings)", source)
        self.assertIn("setAttachRequest({ key: issueToAttach.key, nonce: Date.now() })", source)
        self.assertIn("if (attachRequest?.key !== issue?.key) return", source)
        self.assertIn(
            "if (consumedAttachNonceRef.current === attachRequest?.nonce) return\n"
            "    consumedAttachNonceRef.current = attachRequest?.nonce",
            source,
        )
        # Escape with Undo focused dismisses the affordance, re-expands the
        # attach field, refocuses it, and never reaches the global chain.
        self.assertIn("if (active === undoButtonRef.current) {", source)
        self.assertIn("setRemovedPrLink(null)\n          setShowPrInput(true)", source)
        # Escape inside the settings paste field clears it and is swallowed so
        # the same press cannot close the settings panel.
        self.assertIn("if (document.activeElement === importInputRef.current) {", source)
        self.assertIn("// Disarming beats the global chain so the panel stays open.", source)
        self.assertIn("ref: importInputRef,", source)
        self.assertIn("importInputRef.current?.blur()\n        setPrImportText('')", source)

    def test_shift_keys_attach_attention_and_bulk_undo(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Shift produces uppercase keys ('J'/'K'/'N'), so the letter blocks must
        # match both cases or ⇧j/k and ⇧n silently do nothing.
        self.assertIn(
            "if (event.key === 'j' || event.key === 'J' || event.key === 'ArrowDown' || event.key === 'k' || event.key === 'K' || event.key === 'ArrowUp') {",
            source,
        )
        self.assertIn("const delta = event.key === 'j' || event.key === 'J' || event.key === 'ArrowDown' ? 1 : -1", source)
        self.assertIn("if (event.key === 'n' || event.key === 'N') {", source)
        # ⇧a opens the first visible attention ticket and queues the attach field.
        self.assertIn("['⇧ a', 'Attach PR · attention ticket needing a link'],", source)
        self.assertIn("if (event.key === 'A' && event.shiftKey && !showSettings) {", source)
        self.assertIn("setAttachRequest({ key: attentionTarget.key, nonce: Date.now() })", source)
        self.assertIn("attachRequest = null", source)
        self.assertIn("const consumedAttachNonceRef = useRef(0)", source)
        # Remove all keeps a one-shot snapshot; the manager's empty state offers
        # a single-write restore through the same validation/bounds as writes.
        self.assertIn("removeAllSnapshot = snapshot", source)
        self.assertIn("setRemovedAllLinks(snapshot)", source)
        self.assertIn("function restorePrLinkOverrides(map) {", source)
        self.assertIn(".filter(([key, value]) => ISSUE_KEY_PATTERN.test(key) && prLinkOverrideValid(value?.url ?? value))", source)
        self.assertIn("restorePrLinkOverrides(removedAllLinks)", source)
        self.assertIn("`Undo remove all · ${Object.keys(removedAllLinks).length}`", source)

    def test_readme_keyboard_table_matches_shortcut_rows(self):
        source = PLUGIN.read_text(encoding="utf-8")
        readme = (PLUGIN.parent.parent / "README.md").read_text(encoding="utf-8")
        block = source.split("const SHORTCUT_ROWS = [", 1)[1].split("\n]", 1)[0]
        rows = re.findall(r"\['([^']+)', '([^']+)'\]", block)
        self.assertEqual(len(rows), 20)
        for keys, label in rows:
            self.assertIn(f"| `{keys}` | {label} |", readme)

    def test_caps_lock_attach_import_diff_and_lane_reveal(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Caps Lock produces 'A' without Shift: it must behave like plain `a`,
        # while ⇧a requires an explicit Shift so the two never collide.
        self.assertIn("const isAttachKey = event.key === 'a' || (event.key === 'A' && !event.shiftKey)", source)
        self.assertIn("if ((event.key === 'a' || (event.key === 'A' && !event.shiftKey)) && !selectedKey && !showSettings)", source)
        self.assertIn("if (event.key === 'A' && event.shiftKey && !showSettings)", source)
        # ⇧a prefers attention tickets that do not already carry a manual link.
        self.assertIn("attentionIssues.find(issue => !attachedOverrides[issue.key] && !prStatuses[issue.key]?.pr)", source)
        self.assertIn("|| attentionIssues[0]", source)
        # Import preview badges each row against the stored link: new / update / identical.
        self.assertIn("const status = !prLinks[key] ? 'new' : existingUrl === incomingUrl ? 'same' : 'update'", source)
        self.assertIn("'Replaces the current link'", source)
        self.assertIn("`shrink-0 ${statusClass}`", source)
        # The Import button advertises how many entries it will write.
        self.assertIn("`Import · ${prImportParsed.entries.length}`", source)
        # The Remove-all snapshot lives in module state, so closing and
        # reopening Settings does not lose the Undo affordance.
        self.assertIn("let removeAllSnapshot = null", source)
        self.assertIn("useState(() => removeAllSnapshot)", source)
        self.assertIn("removeAllSnapshot = null", source)
        # Opening a ticket expands its board lane so keyboard navigation can
        # actually reveal the destination row.
        self.assertIn("const containingLane = issueLanes.find(lane => lane.issues.some(item => item.key === key))", source)
        self.assertIn("setCollapsedLanes(current => ({ ...current, [containingLane.key]: false }))", source)
        self.assertIn("}, [collapsedLanes, issueLanes])", source)

    def test_import_tally_identical_skip_and_gh_aware_attention_target(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The preview tallies every parsed entry (not just the first five rows).
        self.assertIn("const importTally = (prImportParsed?.entries || []).reduce(", source)
        self.assertIn("{ new: 0, update: 0, same: 0 }", source)
        self.assertIn("`${importTally.new} new · ${importTally.update} update${importTally.update === 1 ? '' : 's'} · ${importTally.same} identical`", source)
        # Identical entries are not rewritten, keeping savedAt eviction order stable.
        self.assertIn("let alreadyAttached = 0", source)
        self.assertIn("alreadyAttached += 1\n        continue", source)
        self.assertIn("` · ${alreadyAttached} already attached`", source)
        # Import disables itself when nothing would change, and the Enter path agrees.
        self.assertIn("const importAllIdentical = Boolean(prImportParsed?.entries.length) && importTally.same === prImportParsed.entries.length", source)
        # ⇧a prefers attention tickets with neither a manual link nor a gh PR.
        self.assertIn("shiftAttachTarget(sortedVisibleIssues, attentionByKey, readPrLinkOverrides(), readPrStatusCache())", source)

    def test_remove_all_safety_row_copy_badge_a11y_and_draft_ttl(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The armed Remove-all state auto-disarms after 6s and turns red.
        self.assertIn("const disarmTimer = setTimeout(() => setConfirmRemoveAll(false), 6_000)", source)
        self.assertIn("return () => clearTimeout(disarmTimer)", source)
        self.assertIn("className: confirmRemoveAll ? 'text-red-400' : '',", source)
        # Each manager row can copy its own PR URL without a full JSON export.
        self.assertIn("copyTextToClipboard(String(entry.url || ''), `PR link for ${key}`)", source)
        self.assertIn("`Copy the ${key} pull request URL`", source)
        # The PR pill exposes its full description to screen readers, not just 'PR'.
        self.assertIn("const badgeText = state === 'manual'", source)
        self.assertIn("'aria-label': badgeText,", source)
        # The draft counter discloses the 7-day local expiry alongside the limit.
        self.assertIn("auto-expires after ${Math.round(COMMENT_DRAFT_MAX_AGE_MS / 86_400_000)} days", source)

    def test_remove_all_escape_disarm_and_import_density_polish(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Escape disarms the armed Remove-all state (and swallows the press so
        # the settings panel does not close on the same keystroke).
        self.assertIn("}, [confirmRemoveAll])", source)
        self.assertIn("if (confirmRemoveAll) {\n        // Disarming beats", source)
        # The armed tooltip discloses the 6-second auto-cancel.
        self.assertIn("'Click again to remove every manual PR link · auto-cancels in 6s'", source)
        # Any write action (import or per-row remove) disarms a pending confirm.
        self.assertIn("setConfirmRemoveAll(false)\n    setPrLinks(readPrLinkOverrides())\n    setPrImportText('')", source)
        self.assertIn("setConfirmRemoveAll(false)\n                        setLastRemovedRow({ key, url: String(entry.url || '') })\n                        writePrLinkOverride(key, '')", source)
        # The row Copy action is an icon with an accessible name (narrow drawers).
        self.assertIn("'aria-label': `Copy the ${key} pull request URL`,", source)
        self.assertIn("children: jsx(Codicon, { name: 'copy', size: '0.75rem' })", source)
        # Identical preview rows recede so new/update rows stand out.
        self.assertIn("${status === 'same' ? 'opacity-60' : ''}", source)
        # The paste field advertises Enter-to-import.
        self.assertIn("placeholder: 'Paste PR links JSON to restore (Enter imports)'", source)

    def test_live_tally_chip_labels_row_undo_and_escape_order_docs(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The import tally is a polite live region so SR hears counts while typing.
        self.assertIn("'aria-live': 'polite',", source)
        self.assertIn("role: 'status',", source)
        # Lane stat chips and the header PR chip expose full labels, not bare numbers.
        self.assertIn("const laneAttentionLabel = `${laneAttention} ticket", source)
        self.assertIn("const lanePrLabel = `${lanePrCount} ticket", source)
        self.assertIn("'aria-label': laneAttentionLabel,", source)
        self.assertIn("'aria-label': lanePrLabel,", source)
        self.assertEqual(source.count("'aria-label': lanePrLabel,"), 2)
        self.assertIn("'aria-label': quickFilter === 'has-pr'", source)
        # Removing one manager row offers a targeted Undo until the key returns.
        self.assertIn("const [lastRemovedRow, setLastRemovedRow] = useState(null)", source)
        self.assertIn("setLastRemovedRow({ key, url: String(entry.url || '') })", source)
        self.assertIn("lastRemovedRow && !prLinks[lastRemovedRow.key]", source)
        self.assertIn("writePrLinkOverride(lastRemovedRow.key, lastRemovedRow.url)", source)
        self.assertIn("children: 'Undo remove'", source)
        self.assertIn("if (lastRemovedRow && prLinks[lastRemovedRow.key]) setLastRemovedRow(null)", source)
        # The cheatsheet documents the settings-local Escape steps.
        self.assertIn("['Esc', 'Blur → import/disarm → help → panels → filter']", source)

    def test_list_layout_is_a_scan_friendly_ticket_view(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function JiraListRow", source)
        self.assertIn("function JiraList(", source)
        self.assertIn("role: 'table'", source)
        self.assertIn("function settingsViewMode", source)
        self.assertIn("const listView = settingsViewMode(editableSettings) === 'list'", source)
        self.assertIn("updateViewMode('list')", source)
        self.assertIn("? jsxs('div', {\n                className: 'min-h-0 flex-1 overflow-auto", source)
        self.assertIn("children: loadingMore ? 'Loading…' : 'Load more tickets'", source)

    def test_compact_list_is_single_line_and_minimal(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const compact = density === 'compact'", source)
        self.assertIn("const gridTemplateColumns = compact ? 'minmax(14rem, 1fr) 6rem 5rem 8rem 5rem'", source)
        self.assertIn("style: { gridTemplateColumns, borderLeftColor: statusColor(issue) }", source)
        self.assertIn("className: 'min-w-0 truncate text-[0.68rem] text-(--ui-text-secondary)'", source)
        self.assertIn("className: compact ? 'flex min-w-0 flex-nowrap items-center justify-end", source)
        self.assertIn("compact ? 'min-w-[42rem] space-y-0' : 'min-w-[52rem] space-y-1.5'", source)
        self.assertIn("compact ? 'gap-2 border-b border-l-2 border-(--ui-stroke-tertiary) px-2 py-1'", source)
        self.assertIn("className: 'truncate text-xs font-medium text-foreground', children: issue.summary", source)

    def test_subtasks_are_prominent_and_ticket_rows_do_not_wrap(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function JiraIssueHierarchy", source)
        self.assertIn("function JiraParentMarker", source)
        self.assertIn("function ticketWorkPresentation", source)
        self.assertIn("function isCompletedIssue", source)
        self.assertIn("Subtasks ·", source)
        self.assertIn("parent_key", source)
        self.assertIn("truncate whitespace-nowrap", source)
        self.assertIn("flex-nowrap", source)

    def test_jira_toolbar_keeps_views_and_filters_separate_without_hiding_controls(self):
        source = PLUGIN.read_text(encoding="utf-8")

        page = source[source.index("function JiraPage()") :]
        toolbar = page[page.index("      jsxs('header', {") : page.index("      error\n")]
        self.assertIn("flex min-w-0 flex-wrap items-center gap-2", toolbar)
        self.assertNotIn("overflow-x-auto", toolbar)
        self.assertLess(toolbar.index("'aria-label': 'Jira saved view'"), toolbar.index("'aria-label': 'Filter Jira tickets'"))
        self.assertLess(toolbar.index("'aria-label': 'Filter Jira tickets'"), toolbar.index("'aria-pressed': attentionOnly"))
        self.assertEqual(toolbar.count("'aria-label': 'Refresh Jira tickets'"), 1)
        self.assertEqual(toolbar.count("'aria-label': 'Jira Browser settings'"), 1)

    def test_saved_view_management_supports_human_builder_and_per_view_preferences(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function FriendlyViewBuilder", source)
        self.assertIn("Create a saved view", source)
        self.assertIn("onCreateFriendlyView", source)
        self.assertIn("viewPreferences(settings, activeView)", source)
        self.assertIn("updateActiveViewPreference", source)
        self.assertIn("view.layout", source)
        self.assertIn("view.sort", source)
        self.assertIn("view.density", source)

    def test_saved_view_management_supports_duplicate_and_reorder(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const duplicateSavedView = useCallback", source)
        self.assertIn("const reorderSavedView = useCallback", source)
        self.assertIn("onDuplicateView", source)
        self.assertIn("onMoveView", source)
        self.assertIn("Duplicate", source)
        self.assertIn("Move saved view up", source)
        self.assertIn("Move saved view down", source)
        self.assertIn("defaultView: current.defaultView", source)

    def test_saved_view_search_state_is_scoped_and_restored(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const VIEW_STATE_KEY = 'saved-view-state-v1'", source)
        self.assertIn("function readSavedViewState", source)
        self.assertIn("function writeSavedViewState", source)
        self.assertIn("const restoreViewState = useCallback", source)
        self.assertIn("setFilter(String(saved?.filter || '')", source)
        self.assertIn("setQuickFilter(isQuickFilter(saved?.quickFilter)", source)
        self.assertIn("writeSavedViewState(activeView", source)

    def test_refresh_states_preserve_cache_and_offer_retry(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function isLikelyOfflineError", source)
        self.assertIn("Stale · last updated", source)
        self.assertIn("Offline", source)
        self.assertIn("role: 'alert'", source)
        self.assertIn("children: 'Retry'", source)
        self.assertIn("Showing cached results", source)

    def test_ticket_surface_has_quick_filters_sorting_and_density_controls(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const QUICK_FILTER_OPTIONS", source)
        self.assertIn("matchesQuickFilter", source)
        self.assertIn("Quick filter", source)
        self.assertIn("Sort tickets", source)
        self.assertIn("Density", source)
        self.assertIn("sortIssues", source)
        self.assertIn("quickFilter", source)

    def test_quick_filter_counts_are_memoized_and_labeled_as_loaded_only(self):
        source = PLUGIN.read_text(encoding="utf-8")
        page = source[source.index("function JiraPage()") :]

        self.assertIn("const quickFilterCounts = useMemo(() => countQuickFilters(", page)
        self.assertIn("prStatusByKey\n      ))", page)
        self.assertIn("prStatusByKey, workingSessionIds, workStates])", page)
        self.assertIn("nextPageToken ? `${count} loaded` : count", page)
        self.assertIn("nextPageToken ? ' loaded' : ''", page)
        self.assertIn("Counts are for loaded tickets only", page)
        menu = page[page.index("children: QUICK_FILTER_OPTIONS.map(option => {") : page.index("'aria-label': `Needs attention")]
        self.assertNotIn("issues.filter(", menu)

    def test_quick_filter_counts_use_one_pr_snapshot_and_preserve_filter_semantics(self):
        source = PLUGIN.read_text(encoding="utf-8")
        options = source[source.index("const QUICK_FILTER_OPTIONS = [") : source.index("\nfunction viewPreferences")]
        self.assertIn("function countQuickFilters(", source)
        filters = source[source.index("function matchesQuickFilter(") : source.index("\nfunction escapeJqlValue")]
        probe = f"""
          const assert = require('node:assert/strict');
          const hasStoryPointsField = issue => Boolean(issue.has_points_field);
          const storyPointsValue = issue => issue.points ?? null;
          const sessionLinkIdentity = link => link.session_id;
          const readPrStatusCache = () => {{ throw Error('counting must reuse the PR snapshot'); }};
          {options}
          {filters}
          const rows = [
            {{key: 'A-1', has_points_field: true, points: 3, assignee: ''}},
            {{key: 'A-2', has_points_field: false, assignee: 'Pat'}},
            {{key: 'A-3', has_points_field: true, assignee: 'Lee'}}
          ];
          const result = countQuickFilters(
            rows,
            {{'A-1': {{links: []}}, 'A-2': {{links: [{{session_id: 'chat2'}}]}}, 'A-3': {{loading: true}}}},
            {{'A-1': ['Blocked'], 'A-3': ['Stale update']}},
            {{'A-2': 'working'}}, new Set(['chat2']),
            {{'A-1': {{pr: true}}, 'A-2': {{pr: false}}}}
          );
          assert.deepEqual(result, {{all: 3, attention: 2, blocked: 1, unassigned: 1,
            unestimated: 1, 'has-pr': 1, 'no-pr': 1, 'no-linked': 1, working: 1, stale: 1}});
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_working_filter_reacts_to_live_sessions_and_ranks_lowest_last(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("if (/lowest/.test(label)) return 4\n  if (/low/.test(label)) return 3", source)
        self.assertIn("prStatusByKey, quickFilter, workingSessionIds, workStates])", source)

    def test_unchanged_working_session_poll_does_not_rerender_filter_counts(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("function reuseUnchangedSet(", source)
        self.assertIn("setWorkingSessionIds(current => reuseUnchangedSet(current, next))", source)
        helper = source[source.index("function reuseUnchangedSet(") : source.index("\nfunction escapeJqlValue")]
        probe = f"""
          const assert = require('node:assert/strict');
          {helper}
          const old = new Set(['a', 'b']);
          assert.strictEqual(reuseUnchangedSet(old, new Set(['b', 'a'])), old);
          assert.strictEqual(reuseUnchangedSet(new Set(), new Set()).size, 0);
          assert.deepEqual([...reuseUnchangedSet(old, new Set(['c']))], ['c']);
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_unchanged_live_status_snapshot_does_not_rerender_filter_counts(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("function reuseUnchangedRecord(", source)
        self.assertIn("setLiveTicketStates(current => reuseUnchangedRecord(current, next))", source)
        helper = source[source.index("function reuseUnchangedRecord(") : source.index("\nfunction escapeJqlValue")]
        probe = f"""
          const assert = require('node:assert/strict');
          {helper}
          const old = {{ 'A-1': 'working', 'A-2': 'waiting' }};
          assert.strictEqual(reuseUnchangedRecord(old, {{ 'A-2': 'waiting', 'A-1': 'working' }}), old);
          assert.strictEqual(reuseUnchangedRecord({{}}, {{}}).constructor, Object);
          assert.deepEqual(reuseUnchangedRecord(old, {{ 'A-1': 'idle' }}), {{ 'A-1': 'idle' }});
        """
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-1000:])

    def test_ticket_navigation_supports_keyboard_and_previous_next_buttons(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("Keyboard shortcuts", source)
        self.assertIn("event.key === '/'", source)
        self.assertIn("event.key === 'j'", source)
        self.assertIn("event.key === 'k'", source)
        self.assertIn("event.key === 'Escape'", source)
        self.assertIn("'aria-label': 'Previous Jira ticket'", source)
        self.assertIn("'aria-label': 'Next Jira ticket'", source)
        self.assertIn("previousIssueKey", source)
        self.assertIn("nextIssueKey", source)

    def test_workflow_lanes_are_auto_detected_and_cached_from_jira(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const LANE_CACHE_KEY = 'workflow-lane-cache-v1'", source)
        self.assertIn("const [detectedLanes, setDetectedLanes]", source)
        self.assertIn("Promise.all(laneProbeKeys.map", source)
        self.assertIn("/transitions`)", source)
        self.assertIn("writeLaneCache(projectKeys, lanes, status?.base_url)", source)
        self.assertIn("for (const lane of detectedLanes)", source)

    def test_cards_show_cached_linked_work_state(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const WORK_STATE_CACHE_KEY = 'ticket-work-state-cache-v1'", source)
        self.assertIn("const [workStates, setWorkStates]", source)
        self.assertIn("issueLinksPath(issue.id, owner)", source)
        self.assertIn("workState: workStates[issue.key]", source)
        self.assertIn("Linked work", source)

    def test_working_linked_session_highlights_the_ticket_card(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("host.requestProfile(route, 'session.active_list'", source)
        self.assertIn("session.status === 'working'", source)
        self.assertIn("const [workingSessionIds, setWorkingSessionIds]", source)
        self.assertIn("working: linkedWork.some", source)
        self.assertIn("ring-1 ring-(--dt-composer-ring)", source)
        self.assertIn("'aria-label': 'Working'", source)

    def test_existing_linked_work_is_resumed_without_duplicate_session(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const resumeWork = useCallback", source)
        self.assertIn("Resume work", source)
        self.assertIn("onClick: resumeWork", source)
        self.assertIn("onClick: startWork", source)
        self.assertIn("children: busyAction === 'work' ? 'Creating…' : resumableLink ? 'New chat' : 'Open work session'", source)

    def test_unavailable_work_session_explains_how_to_enable_it(self):
        source = PLUGIN.read_text(encoding="utf-8")
        detail = source[source.index("function IssueDetail(") : source.index("function FriendlyViewBuilder(")]
        action = detail[detail.index("children: busyAction === 'resume'") : detail.index("'aria-label': 'Ticket actions'")]

        self.assertIn("const workSessionUnavailable = !mapping && !linkedWorktree?.path", detail)
        self.assertIn("'aria-label': workSessionUnavailable || undefined", action)
        self.assertIn("title: workSessionUnavailable || undefined", action)
        self.assertIn("tabIndex: workSessionUnavailable ? 0 : undefined", action)
        self.assertIn("Link this Jira project to a Hermes Project", detail)

    def test_start_work_paints_visible_seed_without_blocking_on_agent_runtime(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("No agent task has been submitted.", source)
        self.assertIn("profile: ownerRoute.targetProfile || ownerRoute.profile", source)
        self.assertIn("route: ownerRoute", source)
        self.assertIn("profile: route.targetProfile || route.profile", source)
        self.assertIn("route", source)
        self.assertNotIn("expectHistory: false", source)
        start_work = source[source.index("const startWork = useCallback"):source.index("if (!issue) return")]
        self.assertNotIn("if (resumableLink)", start_work)
        self.assertIn("linkedWorktree?.path", start_work)
        self.assertNotIn("await onLinksChanged()", start_work)
        open_call = "await host.openSession(storedId, {"
        self.assertLess(start_work.index(open_call), start_work.index("void onLinksChanged()"))
        self.assertIn("traceWorkOpen(issue.key, 'session-created')", start_work)
        self.assertIn("traceWorkOpen(issue.key, 'open-complete')", start_work)
        self.assertIn("scanGeneration.current += 1", start_work)

    def test_related_chats_are_discovered_and_attachable(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("host.listPersistedSessions(ownerRoute, { profile, limit: 500 })", source)
        self.assertIn("host.requestProfile(ownerRoute, 'projects.project_sessions'", source)
        self.assertIn("session_limit: 20_000", source)
        self.assertIn("api(`/sessions/${encodeURIComponent(jiraProjectKey)}`)", source)
        self.assertIn("Likely related chats", source)
        self.assertIn("const attachRelatedChat = useCallback", source)
        self.assertIn("Scan chats", source)

    def test_manual_link_can_move_a_chat_from_another_ticket(self):
        source = PLUGIN.read_text(encoding="utf-8")
        manual = source[source.index("const linkCurrent"):source.index("const attachRelatedChat")]
        attach = source[source.index("const attachRelatedChat"):source.index("const unlinkChat")]
        automatic = source[source.index("const worktreeSessions"):source.index("const related =")]
        self.assertIn("move_existing: true", manual)
        self.assertIn("move_existing: true", attach)
        self.assertIn("move_existing: false", automatic)

        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const DETACHED_CHAT_LINKS_KEY = 'detached-ticket-chat-links-v1'", source)
        self.assertIn("const unlinkChat = useCallback", source)
        self.assertIn("method: 'DELETE'", source)
        self.assertIn("onUnlink: unlinkChat", source)
        self.assertIn("name: 'link-break'", source)
        self.assertIn("!detachedIds.has(linkIdentity)", source)

    def test_linked_worktree_auto_attaches_its_chats(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const WORKTREE_LINKS_KEY = 'ticket-worktree-links-v1'", source)
        self.assertIn("const [linkedWorktree, setLinkedWorktree]", source)
        self.assertIn("session.cwd === linkedWorktree.path", source)
        self.assertIn("title: 'Use the current chat’s worktree · auto-link its chats'", source)
        self.assertIn("Use current worktree", source)

    def test_worktree_scan_is_generation_guarded_and_continues_after_bad_session(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const scanGeneration = useRef(0)", source)
        self.assertIn("const isCurrent = () => generation === scanGeneration.current", source)
        self.assertIn("failedCount += 1", source)
        self.assertIn("disabled: scanning", source)

    def test_attention_does_not_infer_missing_work_after_link_refresh_failure(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("!workState.refreshFailed", source)

    def test_agent_can_prepare_a_linked_jira_update_draft(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const draftJiraUpdate = useCallback", source)
        self.assertIn("title: `Jira update ${issue.key}`", source)
        self.assertIn("host.requestProfile(route, 'prompt.submit'", source)
        self.assertIn("Do not mutate Jira", source)
        self.assertIn("Draft update", source)

    def test_transition_suggestion_requires_explicit_click(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const suggestedTransition = useMemo", source)
        self.assertIn("Suggested next step", source)
        self.assertIn("Apply suggestion", source)

    def test_explicit_jira_mutations_reuse_bounded_retry_keys(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const MUTATION_KEY_CACHE_LIMIT = 32", source)
        self.assertIn("function mutationKeyFor", source)
        self.assertIn("function forgetMutationKey", source)
        self.assertIn("mutationKeyFor('comment'", source)
        self.assertIn("mutationKeyFor('suggestion'", source)
        self.assertIn("mutationKeyFor('transition'", source)
        self.assertIn("mutationKeyFor('drag'", source)
        self.assertNotIn("const mutationKey = newMutationKey()", source)

    def test_transition_retry_keys_survive_until_followup_refresh_succeeds(self):
        source = PLUGIN.read_text(encoding="utf-8")
        suggestion = source[source.index("const applySuggestedTransition"):source.index("const draftJiraUpdate")]
        manual = source[source.index("const moveIssue = useCallback"):source.index("const startWork = useCallback")]
        drag = source[source.index("const moveIssueToLane"):source.index("const beginDrawerResize")]

        self.assertLess(suggestion.index("const [updated, choices]"), suggestion.index("forgetMutationKey('suggestion'"))
        self.assertLess(manual.index("const [updated, choices]"), manual.index("forgetMutationKey('transition'"))
        self.assertLess(drag.index("const updated = await api"), drag.index("forgetMutationKey('drag'"))

    def test_mutation_retry_cache_pins_unresolved_keys_and_rejects_when_full(self):
        source = PLUGIN.read_text(encoding="utf-8")
        cache = source[source.index("function mutationKeyFor"):source.index("function normaliseIssueKey")]

        self.assertIn("status: 'unresolved'", cache)
        self.assertIn("candidate.status === 'completed'", cache)
        self.assertIn("Jira mutation retry capacity is full", cache)
        self.assertNotIn("mutationKeyCache.delete(mutationKeyCache.keys().next().value)", cache)
        self.assertIn("entry.status = 'completed'", cache)

    def test_comment_cleanup_happens_after_every_followup_succeeds(self):
        source = PLUGIN.read_text(encoding="utf-8")
        comment = source[source.index("const postComment = useCallback"):source.index("const moveIssue = useCallback")]

        self.assertLess(comment.index("setCommentDraft('')"), comment.index("onIssueChanged"))
        self.assertLess(comment.index("onIssueChanged"), comment.index("host.notify"))
        self.assertLess(comment.index("host.notify"), comment.index("forgetMutationKey('comment'"))
        self.assertIn("catch (cause)", comment)

    def test_explicit_jira_mutations_send_high_entropy_idempotency_keys(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("crypto.randomUUID()", source)
        self.assertIn("body: { body, idempotency_key: mutationKey }", source)
        self.assertIn("body: { transition_id: suggestedTransition.id, idempotency_key: mutationKey }", source)
        self.assertIn("body: { transition_id: transitionId, idempotency_key: mutationKey }", source)
        self.assertIn("body: { transition_id: transition.id, idempotency_key: mutationKey }", source)

    def test_attention_view_surfaces_tracking_friction(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function issueAttentionReasons", source)
        self.assertIn("const [attentionOnly, setAttentionOnly]", source)
        self.assertIn("Needs attention", source)
        self.assertIn("attentionReasons", source)

    def test_jira_uses_the_same_route_canvas_contract_as_kanban(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("data: { path: ROUTE }", source)
        self.assertIn("render: () => jsx(JiraPage, {})", source)
        self.assertIn("run: () => host.navigate(ROUTE)", source)

    def test_exact_ticket_links_round_trip_through_the_hash_route(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const ISSUE_QUERY_PARAM = 'issue'", source)
        self.assertIn("const ISSUE_KEY_PATTERN = /^[A-Z][A-Z0-9]+-\\d+$/", source)
        self.assertIn("function issueKeyFromHash", source)
        self.assertIn("function hashHasIssueParam", source)
        self.assertIn("window.addEventListener('hashchange', syncFromHash)", source)
        self.assertIn("params.delete(ISSUE_QUERY_PARAM)", source)
        self.assertIn("host.navigate(jiraRoute(key))", source)
        self.assertIn("const key = normaliseIssueKey(issueKey)", source)
        self.assertIn("if (!selectedKey && hashHasIssueParam()) host.navigate(jiraRoute(''))", source)

    def test_ticket_companion_is_opt_in_deduplicated_and_read_only(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const companionDisposers = new Map()", source)
        self.assertIn("typeof host.openWorkspace !== 'function'", source)
        self.assertIn("const companionId = `${ID}:ticket:${issue.key}`", source)
        self.assertIn("const disposer = host.openWorkspace(companionId", source)
        self.assertIn("dock: { pane: 'workspace', pos: 'right' }", source)
        self.assertIn("companionDisposers.set(companionId, disposer)", source)
        self.assertIn("readOnly: true", source)
        self.assertIn("Pin beside chat", source)
        self.assertIn("host.navigate('/')", source)
        self.assertNotIn("host.revealPane", source)
        self.assertIn("host.notify({ kind: 'warning', message: 'Pinning tickets beside chat is not supported", source)

    def test_companion_cleanup_uses_the_native_disposer(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("for (const disposer of companionDisposers.values()) disposer()", source)
        self.assertIn("companionDisposers.delete(companionId)", source)
        jira_page = source[source.index("function JiraPage()") : source.index("export default")]
        self.assertNotIn("for (const disposer of companionDisposers.values()) disposer()", jira_page)

    def test_ticket_palette_picker_is_not_registered_without_a_safe_sdk_prompt(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertNotIn("Open Jira ticket…", source)

    def test_session_link_identity_includes_connection_and_profile_owner(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function linkId(link)", source)
        self.assertIn("function sessionLinkIdentity", source)
        self.assertIn("owner.connectionId", source)
        self.assertIn("owner.profileName", source)
        self.assertIn("const connectionId = String(owner.connectionId || owner.connection_id || '').trim()", source)
        self.assertIn("const profileName = String(owner.profileName || owner.profile_name || owner.profile || '').trim()", source)
        self.assertIn("const targetProfile = String(owner.targetProfile || owner.target_profile || '').trim()", source)
        self.assertIn("return `${connectionId}::${profileName}::${targetProfile}::${sessionId}`", source)
        self.assertIn("sessionLinkIdentity(link)", source)
        self.assertIn("`${link.issue_id}:${sessionLinkIdentity(link)}`", source)

    def test_session_links_resolve_saved_owner_for_open_and_unlink(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const route = await resolveSessionRoute(link)", source)
        self.assertIn("route,", source[source.index("const route = await resolveSessionRoute(link)") :])
        self.assertIn("connection_id: owner.connectionId", source)
        self.assertIn("profile_name: owner.profileName", source)
        self.assertIn("target_profile: owner.targetProfile", source)
        self.assertIn("new URLSearchParams({ connection_id: owner.connectionId, profile_name: owner.profileName, target_profile: owner.targetProfile })", source)

    def test_session_link_scans_and_verifies_through_the_owner_route(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("host.profileRoutes()", source)
        self.assertIn("host.listPersistedSessions(route", source)
        self.assertIn("host.requestProfile(route, 'session.list'", source)
        self.assertIn("const focusedOwner = readFocusedSessionOwner()", source)
        self.assertIn("mergeBackendDetachedLinks", source)
        self.assertIn("const linkIdentity = sessionLinkIdentity(linkCandidate)", source)
        self.assertIn("detachedIds.has(linkIdentity)", source)
        self.assertIn("link?.detached_at", source)
        self.assertIn("available: undefined", source)

    def test_backend_detach_tombstones_are_consumed_separately_from_active_links(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function filterDetachedLinks(issueKey, links, backendDetached = [], jiraOrigin = '')", source)
        self.assertIn("mergeBackendDetachedLinks(issueKey, backendDetached, jiraOrigin)", source)
        self.assertIn("filterDetachedLinks(issue.key, result?.links, result?.detached, status?.base_url)", source)
        self.assertIn("filterDetachedLinks(detail.key, result?.links, result?.detached, status?.base_url)", source)
        self.assertIn("filterDetachedLinks(nextDetail.key, linksResult?.links, linksResult?.detached, status?.base_url)", source)

    def test_foreign_owner_scans_never_mix_ambient_backend_sessions(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("function isAmbientOwnerRoute", source)
        self.assertIn("isAmbientOwnerRoute(ownerRoute) && jiraProjectKey", source)
        self.assertIn("const route = await resolveFocusedSessionRoute()", source)

    def test_focused_owner_resolution_and_detach_are_fail_closed(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("host.state?.connectionId?.get?.()", source)
        self.assertIn("host.activeConnectionId?.()", source)
        self.assertGreaterEqual(source.count("const route = await resolveFocusedSessionRoute()"), 4)
        self.assertIn("sessionLinkIdentity(candidate) !== sessionLinkIdentity(linkCandidate)", source)
        self.assertNotIn("Older running backends do not have the DELETE route yet", source)
        self.assertIn("if (result?.unlinked !== true) throw new Error", source)
        self.assertIn("clear_detachment: true", source)
        self.assertIn("clear_detachment: false", source)
        self.assertIn("writeChatDetached(issue.key, linkCandidate, false, status?.base_url)", source)
        self.assertIn("if (!owner) throw new Error('The focused chat owner is ambiguous or unavailable.')", source)
        owner = source[source.index("function ownerFromLink"):source.index("function ownerFromRoute")]
        self.assertIn("const rawTargetProfile = link?.target_profile ?? link?.targetProfile", owner)
        self.assertIn("if (!connectionId || !profileName || !targetProfile) return null", owner)
        self.assertNotIn("|| profileName).trim() || profileName", owner)

    def test_focused_profile_fallback_is_ambient_verified_and_fail_closed(self):
        source = PLUGIN.read_text(encoding="utf-8")
        focused_owner = source[source.index("function readFocusedSessionOwner"):source.index("function ownerFromLink")]

        self.assertIn("const ambientProfileName", focused_owner)
        self.assertIn("const focusedProfileName", focused_owner)
        self.assertIn("focusedProfileName && focusedProfileName !== ambientProfileName", focused_owner)
        self.assertIn("if (!ambientProfileName) return null", focused_owner)
        self.assertNotIn("|| 'default'\n  ).trim() || 'default'\n  return { connectionId, profileName, targetProfile: profileName }", focused_owner)

    def test_owner_route_resolution_rejects_ambiguous_same_owner_routes(self):
        source = PLUGIN.read_text(encoding="utf-8")
        resolver = source[source.index("async function resolveSessionRoute"):source.index("async function resolveFocusedSessionRoute")]

        self.assertIn("const ownerMatches = (Array.isArray(routes) ? routes : []).filter", resolver)
        self.assertIn("if (ownerMatches.length > 1) throw new Error", resolver)
        self.assertNotIn(").find(candidate =>", resolver)

    def test_owner_route_resolution_rejects_duplicate_owner_before_target_matching(self):
        source = PLUGIN.read_text(encoding="utf-8")
        resolver = source[source.index("async function resolveSessionRoute"):source.index("async function resolveFocusedSessionRoute")]

        self.assertIn("const ownerMatches = (Array.isArray(routes) ? routes : []).filter", resolver)
        self.assertIn("if (ownerMatches.length > 1) throw new Error", resolver)
        self.assertIn("const matches = ownerMatches.filter", resolver)
        self.assertLess(
            resolver.index("if (ownerMatches.length > 1) throw new Error"),
            resolver.index("const matches = ownerMatches.filter"),
        )

    def test_working_session_snapshot_clears_when_focused_owner_cannot_be_resolved(self):
        source = PLUGIN.read_text(encoding="utf-8")
        refresh = source[source.index("const refreshWorkingSessions"):source.index("const timer = window.setInterval", source.index("const refreshWorkingSessions"))]

        self.assertIn("route = await resolveFocusedSessionRoute()", refresh)
        self.assertIn("if (alive && !isConfirmedTransientRpcFailure(cause)) {\n            setWorkingSessionIds(current => reuseUnchangedSet(current, new Set()))", refresh)
        self.assertIn("function isConfirmedTransientRpcFailure", source)
        self.assertIn("error?.transient === true", source)

    def test_raw_legacy_detach_tombstones_suppress_all_owner_qualified_matches(self):
        source = PLUGIN.read_text(encoding="utf-8")
        filtering = source[source.index("function filterDetachedLinks"):source.index("function clampDrawerWidth")]

        self.assertIn("const legacyLocalDetachIds = new Set", filtering)
        self.assertIn("legacyLocalDetachIds.add(sessionId)", filtering)
        self.assertIn("!legacyLocalDetach", filtering)
        self.assertLess(filtering.index("legacyLocalDetachIds.add(sessionId)"), filtering.index(".filter(link =>"))

    def test_new_chat_uses_focused_route_without_target_profile_match(self):
        source = PLUGIN.read_text(encoding="utf-8")
        start_work = source[source.index("const startWork = useCallback"):source.index("if (!issue) return")]

        self.assertIn("ownerRoute = await resolveFocusedSessionRoute()", start_work)
        self.assertNotIn("resolveSessionRoute(readFocusedSessionOwner())", start_work)

    def test_start_work_cleanup_never_uses_ambient_session_delete(self):
        source = PLUGIN.read_text(encoding="utf-8")
        start_work = source[source.index("const startWork = useCallback"):source.index("if (!issue) return")]

        self.assertIn("host.requestProfile(ownerRoute, 'session.delete'", start_work)
        self.assertNotIn("host.request('session.delete'", start_work)

    def test_ownerless_links_and_legacy_detach_tombstones_fail_closed(self):
        source = PLUGIN.read_text(encoding="utf-8")

        availability = source[source.index("function linkAvailability"):source.index("function filterDetachedLinks")]
        self.assertIn("if (!owner) return { ...link, available: false }", availability)
        self.assertIn("if (!focused) return { ...link, available: false }", availability)
        detach = source[source.index("function writeChatDetached"):source.index("function mergeBackendDetachedLinks")]
        self.assertIn("ids.delete(sessionId)", detach)
        filtering = source[source.index("function filterDetachedLinks"):source.index("function clampDrawerWidth")]
        self.assertIn("const legacyLocalDetach = legacyLocalDetachIds.has(sessionId)", filtering)

    def test_ambient_owner_route_matches_the_full_active_route_tuple(self):
        source = PLUGIN.read_text(encoding="utf-8")
        ambient = source[source.index("function isAmbientOwnerRoute"):source.index("function sessionIdFromRow")]

        self.assertIn("activeTargetProfile", ambient)
        self.assertIn("String(route?.targetProfile || '').trim() === activeTargetProfile", ambient)

    def test_draft_update_is_fully_routed_and_fails_closed(self):
        source = PLUGIN.read_text(encoding="utf-8")
        draft = source[source.index("const draftJiraUpdate"):source.index("const postComment")]

        self.assertIn("const route = await resolveFocusedSessionRoute()", draft)
        self.assertIn("host.requestProfile(route, 'session.create'", draft)
        self.assertIn("host.requestProfile(route, 'prompt.submit'", draft)
        self.assertIn("route: route", draft)
        self.assertIn("profile: route.targetProfile || route.profile", draft)
        self.assertIn("host.requestProfile(route, 'session.delete'", draft)
        self.assertNotIn("host.request('session.create'", draft)
        self.assertNotIn("host.request('prompt.submit'", draft)
        self.assertNotIn("host.request('session.delete'", draft)

    def test_focused_link_rechecks_owner_identity_before_posting(self):
        source = PLUGIN.read_text(encoding="utf-8")
        link = source[source.index("const linkCurrent"):source.index("useEffect(() => {", source.index("const linkCurrent"))]

        self.assertIn("const focusedOwner = readFocusedSessionOwner()", link)
        self.assertIn("const focusedLinkIdentity = sessionLinkIdentity", link)
        self.assertIn("const currentLinkIdentity = sessionLinkIdentity", link)
        self.assertIn("if (currentLinkIdentity !== focusedLinkIdentity) throw new Error", link)
        self.assertIn("scanGeneration.current += 1", link)

    def test_present_but_invalid_focused_owner_never_uses_ambient_fallback(self):
        source = PLUGIN.read_text(encoding="utf-8")
        focused_owner = source[source.index("function readFocusedSessionOwner"):source.index("function ownerFromLink")]

        self.assertIn("if (focusedOwnerAtom != null)", focused_owner)
        self.assertIn("if (typeof focusedOwnerAtom.get !== 'function') return null", focused_owner)
        self.assertIn("if (!connectionId || !profileName", focused_owner)
        self.assertLess(
            focused_owner.index("if (!connectionId || !profileName"),
            focused_owner.index("const connectionId = String(\n    host.state?.connectionId"),
        )

    def test_every_link_write_is_owner_qualified_and_manual_writes_clear_detachment(self):
        source = PLUGIN.read_text(encoding="utf-8")
        writes = []
        cursor = 0
        while True:
            start = source.find("await api('/links'", cursor)
            if start < 0:
                break
            end = source.find("\n      })", start)
            self.assertGreater(end, start)
            writes.append(source[start:end])
            cursor = end + 1

        self.assertEqual(len(writes), 4)
        self.assertEqual(sum("...sessionOwnerFields(owner)" in write for write in writes), 4)
        self.assertEqual(sum("clear_detachment: true" in write for write in writes), 3)
        self.assertEqual(sum("clear_detachment: false" in write for write in writes), 1)
        self.assertIn("new URLSearchParams({ connection_id: owner.connectionId, profile_name: owner.profileName, target_profile: owner.targetProfile })", source)

    def test_issue_link_reads_require_the_active_owner_query(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("function readActiveOwner()", source)
        self.assertIn("function issueLinksPath(issueId, owner)", source)
        self.assertIn("new URLSearchParams(sessionOwnerFields(owner))", source)
        self.assertGreaterEqual(source.count("issueLinksPath("), 4)

    def test_detached_local_keys_are_scoped_by_jira_origin_and_full_owner(self):
        source = PLUGIN.read_text(encoding="utf-8")
        detached = source[source.index("function normaliseJiraOrigin"):source.index("function linkAvailability")]

        self.assertIn("function detachedChatStorageKey(issueKey, jiraOrigin, link)", detached)
        self.assertIn("const origin = normaliseJiraOrigin(jiraOrigin)", detached)
        self.assertIn("owner?.connectionId", detached)
        self.assertIn("owner?.profileName", detached)
        self.assertIn("owner?.targetProfile", detached)
        self.assertIn("encodeURIComponent", detached)
        self.assertIn("writeChatDetached(issue.key, link, true, status?.base_url)", source)
        self.assertIn("readDetachedChatIds(issue.key, status?.base_url, owner)", source)
        self.assertIn("clear_detachment: true", source[source.index("const startWork"):source.index("if (!issue) return", source.index("const startWork"))])


    def test_round31_draft_markers_attention_bells_and_rollup_labels(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Comment-draft dots render in list rows and board cards from the
        # same cache scope the detail drawer writes into.
        self.assertIn("let activeDraftScope = ''", source)
        self.assertIn("activeDraftScope = activeCacheScope", source)
        self.assertIn("function commentDraftMarker(scope, issueKey)", source)
        self.assertEqual(source.count("commentDraftMarker(activeDraftScope, issue.key)"), 2)
        self.assertIn("'aria-label': 'Unsent comment draft saved'", source)

        # The story-points rollup chip names itself and exposes sort state.
        rollup = source[source.index("storyPointsRollup !== null"):source.index("headerPrCount > 0")]
        self.assertIn("'aria-pressed': activeViewPreferences.sort === 'points'", rollup)
        self.assertIn("'Total story points for the tickets shown · click to sort by updated'", rollup)

        # The visible-count chip reads as a sentence, not a bare number.
        self.assertIn("'aria-label': `${visibleIssues.length} ticket${visibleIssues.length === 1 ? '' : 's'} shown`", source)

        # The PR-mismatch notice discloses both full URLs on hover.
        self.assertIn("title: `Manual ${manualPullRequestValid.url", source)

        # The PR-link manager rows show an attention bell with a full reason
        # tooltip, plumbed from JiraPage attention state.
        self.assertIn("attentionByKey = {},", source)
        call = source[source.index("jsx(SettingsDrawer, {"):source.index("state: settingsState")]
        self.assertIn("attentionByKey,", call)
        manager = source[source.index("prLinkRows.map"):source.index("Remove the ${key} pull request attachment")]
        self.assertIn("attentionByKey[key]?.length", manager)
        self.assertIn("title: attentionByKey[key].join(' · ')", manager)
        self.assertIn("name: 'bell'", manager)


    def test_round32_prewarm_manager_navigation_and_toggle_states(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Background PR prewarm: bounded queue, same endpoint/mapping as the
        # drawer, session-scoped dedupe, and honest unknown-on-error behavior.
        self.assertIn("const PR_PREWARM_LIMIT = 6", source)
        self.assertIn("slice(0, PR_PREWARM_LIMIT)", source)
        self.assertIn("prPrewarmRef", source)
        self.assertEqual(source.count("repository-context"), 3)
        self.assertIn("transport failure: badge stays unknown", source)
        prewarm = source[source.index("const prPrewarmRef = useRef"):source.index("const storyPointsRollup = useMemo(")]
        self.assertIn("writePrStatus(key, pullRequest", prewarm)
        self.assertNotIn(".catch(() => { /* transport failure: badge stays unknown, never absent */ })\n            if", prewarm)

        # PR-link manager rows: click the key to open the ticket (settings
        # closes via openTicket) and show the local comment-draft dot.
        self.assertIn("onOpenTicket,", source[source.index("function SettingsDrawer({"):source.index("const prLinkRows = Object.entries(prLinks)")])
        self.assertIn("onOpenTicket: openTicket,", source)
        manager = source[source.index("prLinkRows.map"):source.index("Remove the ${key} pull request attachment")]
        self.assertIn("onClick: () => onOpenTicket?.(key)", manager)
        self.assertIn("commentDraftMarker(activeDraftScope, key)", manager)

        # The drawer header shows the draft dot next to the selected key.
        self.assertIn("commentDraftMarker(activeDraftScope, selectedKey)", source)

        # Lane disclosure buttons report their expanded state.
        self.assertEqual(source.count("'aria-expanded': false"), 1)
        self.assertEqual(source.count("'aria-expanded': true"), 1)


    def test_round33_filter_freshness_prewarm_priority_pin_detected_and_labels(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # The memoized PR snapshot changes when the cache version advances,
        # invalidating both visible tickets and the filter counts.
        self.assertIn("const prStatusByKey = useMemo(() => readPrStatusCache(), [prStatusVersion])", source)
        self.assertIn("liveTicketStates, prStatusByKey, quickFilter", source)

        # Prewarm: attention tickets first, offline sessions skip without
        # burning their once-per-session attempt.
        prewarm = source[source.index("const prPrewarmRef = useRef"):source.index("const storyPointsRollup = useMemo(")]
        self.assertIn("if (!navigator.onLine) return () => { alive = false }", prewarm)
        self.assertIn("attentionByKey[right.key]", prewarm)
        self.assertIn("}, [attentionByKey, selectedKey, sortedVisibleIssues])", prewarm)

        # Lane points chips (rail + header) name their value.
        self.assertEqual(
            source.count("'aria-label': `${lanePoints} story points in this lane`"),
            2,
        )

        # The drawer offers one-click pinning of the detected PR as a local
        # manual link (local storage only, no Jira mutation).
        pin = source[source.index("!prOverride && detectedPullRequest && !showPrInput"):source.index("prOverride\n                ? jsxs")]
        self.assertIn("Pin detected #", pin)
        self.assertIn("writePrLinkOverride(issue.key, url)", pin)
        self.assertIn("pinned to ${issue.key}", pin)
        self.assertIn("children: 'Add manual link'", pin)

        # The settings gear tooltip discloses attention among linked tickets.
        self.assertIn("const prLinkAttentionCount", source)
        self.assertIn("` · ${prLinkAttentionCount} need attention`", source)


    def test_round34_repinto_prewarm_ttl_selected_skip_and_density_switch(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Prewarm: session Map with TTL-based retry, skips the ticket the
        # drawer is already fetching, re-runs when the selection changes.
        prewarm = source[source.index("const prPrewarmRef = useRef"):source.index("const storyPointsRollup = useMemo(")]
        self.assertIn("useRef(new Map())", prewarm)
        self.assertIn("key !== selectedKey", prewarm)
        self.assertIn("Date.now() - prPrewarmRef.current.get(key) > PR_STATUS_MAX_AGE_MS", prewarm)
        self.assertIn("prPrewarmRef.current.set(key, Date.now())", prewarm)
        self.assertIn("}, [attentionByKey, selectedKey, sortedVisibleIssues])", prewarm)

        # The mismatch notice is a polite live region offering a one-click
        # re-pin to the detected pull request (local write only, read-only
        # connections keep it hidden).
        start = source.index("\n              prMismatch")
        mismatch = source[start:start + 1800]
        self.assertIn("role: 'status'", mismatch)
        self.assertIn("Re-pin to #${pullRequest.number}", mismatch)
        self.assertIn("Re-pinned ${issueKey} to detected", mismatch)
        self.assertIn("!readOnly\n                        ? jsx(Button", mismatch)
        self.assertEqual(source.count("writePrLinkOverride(issue.key, url)"), 1)

        # The density control exposes switch semantics.
        self.assertEqual(source.count("role: 'switch'"), 1)
        self.assertIn("'aria-checked': activeViewPreferences.density === 'compact'", source)


    def test_round35_drift_manager_shared_attach_target_and_preview_bells(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # Drift: shared detector, manager sweep, per-row re-pin chips.
        self.assertIn("function prLinkDrift(entry, detected)", source)
        self.assertIn("function shiftAttachTarget(issues, attentionByKey, attachedOverrides, prStatuses)", source)
        settings = source[source.index("function SettingsDrawer({"):source.index("const prImportParsed = parsePrLinkImport")]
        self.assertIn("const prDriftByKey = new Map(", settings)
        self.assertIn("const repinAllDrifted = () => {", settings)
        self.assertIn("setConfirmRemoveAll(false)\n    for (const [key, drift] of prDriftByKey)", settings)
        self.assertIn("`Re-pin ${prDriftByKey.size} drifted`", source)
        self.assertIn("` · ${prDriftByKey.size} drifted`", source)
        manager = source[source.index("prLinkRows.map"):source.index("Remove the ${key} pull request attachment")]
        self.assertIn("const drift = prDriftByKey.get(key)", manager)
        self.assertIn("`≠ #${drift.number}`", manager)
        self.assertIn("Re-pinned ${key} to the detected pull request", manager)

        # The ⇧a handler and the prewarm queue share one target helper over
        # the same sorted list, so the warmed queue always contains the key
        # ⇧a would pick — even when it falls outside the top slice.
        self.assertEqual(source.count("shiftAttachTarget(sortedVisibleIssues, attentionByKey"), 2)
        self.assertIn("queue.push(attachTargetKey)", source)

        # Import preview rows carry attention bells, like manager rows.
        preview_at = source.index("const statusLabel = status ===")
        self.assertIn("attentionByKey[key]?.length", source[preview_at:preview_at + 1200])

        # The gear tooltip reports drift alongside attention.
        self.assertIn("const prLinkDriftCount", source)
        self.assertIn("` · ${prLinkDriftCount} drifted`", source)


    def test_round36_dev_section_binding_bugfix_badge_pin_and_repin_undo(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # BUG FIX (round 34 regression): IssueDevelopmentSection is a
        # module-level component — issue/setPrOverride/readOnly never existed
        # in its scope, so the mismatch notice crashed at render and its
        # Re-pin crashed on click. The bindings now arrive as props.
        dev_start = source.index("function IssueDevelopmentSection(")
        dev = source[dev_start:source.index("\nfunction ", dev_start + 10)]
        self.assertIn(
            "function IssueDevelopmentSection({ context, manualPullRequest, issueKey = '', readOnly = false, onOverrideChanged = null })",
            dev,
        )
        self.assertIn("writePrLinkOverride(issueKey, url)", dev)
        self.assertIn("onOverrideChanged?.()", dev)
        self.assertNotIn("setPrOverride", dev)
        self.assertNotIn("issue.key", dev)
        self.assertNotIn("issue?.key", dev)
        self.assertIn("issueKey: issue?.key,\n        onOverrideChanged: () => setPrOverride(", source)

        # List and board rows pin a detected-but-unattached PR straight from
        # the badge — a local write, pointer-only so rows stay valid buttons.
        self.assertEqual(source.count("jsx(PrStatusBadge, { entry: prEntry, issueKey: issue.key })"), 2)
        badge_start = source.index("function PrStatusBadge(")
        badge = source[badge_start:source.index("\nfunction ", badge_start + 10)]
        self.assertIn("const canPin = Boolean(issueKey && Number(entry.number) > 0 && pinUrl && !readPrLinkOverrides()[issueKey])", badge)
        self.assertIn("event.stopPropagation()", badge)
        self.assertIn("writePrLinkOverride(issueKey, pinUrl)", badge)
        self.assertIn("pinned to ${issueKey}.", badge)
        self.assertIn("'aria-hidden': 'true'", badge)

        # Re-pin is undoable: the row chip and the sweep both stash the
        # previous links first and offer a one-shot restore.
        settings = source[source.index("function SettingsDrawer({"):source.index("const prImportParsed = parsePrLinkImport")]
        self.assertIn("const [lastRepinned, setLastRepinned] = useState(null)", settings)
        self.assertIn("const [repinSnapshot, setRepinSnapshot] = useState(null)", settings)
        self.assertIn("for (const [key] of prDriftByKey) previousLinks[key] = prLinks[key]", settings)
        self.assertIn("children: `Undo re-pin · ${Object.keys(repinSnapshot).length}`", source)
        self.assertIn("restorePrLinkOverrides({ [key]: lastRepinned.previous })", source)
        self.assertIn("children: 'Undo re-pin'", source)
        self.assertIn("if (lastRepinned && !prLinks[lastRepinned.key]) setLastRepinned(null)", source)

        # The import tally discloses attention among pasted keys, and the
        # prewarm warms more badges now that the ⇧a target is guaranteed.
        self.assertIn("const importAttentionCount", source)
        tally_at = source.index("importTally.same} identical`")
        self.assertIn("importAttentionCount", source[tally_at:tally_at + 220])
        self.assertIn("const PR_PREWARM_LIMIT = 6", source)

        # Collapse-all exposes expanded state like the per-lane toggles.
        self.assertIn("'aria-expanded': !allLanesCollapsed", source)


    def test_round37_attached_state_mismatch_undo_shift_pin_and_live_manager(self):
        source = PLUGIN.read_text(encoding="utf-8")

        # A cached detection shadows the manual entry in readPrStatusCache,
        # so the badge pin checks overrides directly — already-attached
        # tickets never offer the pin again, and a pin also fetches detection
        # data (positive results only; negatives must not hide the badge).
        badge_start = source.index("function PrStatusBadge(")
        badge = source[badge_start:source.index("\nfunction ", badge_start + 10)]
        self.assertIn("!readPrLinkOverrides()[issueKey]", badge)
        self.assertIn("repository-context", badge)
        self.assertIn("if (pullRequest) writePrStatus(issueKey, { number:", badge)
        self.assertNotIn("{ pr: false, fetchedAt: Date.now() }", badge)

        # The drawer's mismatch Re-pin stashes the replaced link and offers a
        # one-shot Undo keyed to the same ticket.
        dev_start = source.index("function IssueDevelopmentSection(")
        dev = source[dev_start:source.index("\nfunction ", dev_start + 10)]
        self.assertIn("const [repinUndo, setRepinUndo] = useState(null)", dev)
        self.assertIn("setRepinUndo({ key: issueKey, previous: manualPullRequest })", dev)
        self.assertIn("repinUndo.key === issueKey && manualPullRequestValid && !prMismatch", dev)
        self.assertIn("restorePrLinkOverrides({ [issueKey]: repinUndo.previous })", dev)

        # ⇧p pins the detected PR for the current/top ticket; plain p (chat
        # pin) is lowercase-exact so the two never collide, and the warning
        # path leaves an already-attached ticket untouched.
        pin_at = source.index("if (event.key === 'P' && event.shiftKey) {")
        pin_key = source[pin_at:source.index("if (event.key === 'o')", pin_at)]
        self.assertIn("const pinTargetKey = String(selectedKey || sortedVisibleIssues[0]?.key || '').trim()", pin_key)
        self.assertIn("readPrLinkOverrides()[pinTargetKey]", pin_key)
        self.assertIn("writePrLinkOverride(pinTargetKey, detectedUrl)", pin_key)
        self.assertIn("pinned to ${pinTargetKey}.", pin_key)
        block = source.split("const SHORTCUT_ROWS = [", 1)[1].split("\n]", 1)[0]
        self.assertIn("['⇧ p', 'Pin detected PR for this ticket'],", block)

        # The open PR-link manager refreshes when links change elsewhere —
        # badge pins, imports, and drawer writes fan out through subscribers.
        self.assertIn("useEffect(() => subscribePrStatus(() => setPrLinks(readPrLinkOverrides())), [])", source)


    def test_editable_settings_precedes_effect_dependency_evaluation(self):
        source = PLUGIN.read_text(encoding="utf-8")
        declaration = source.index("const editableSettings = useMemo(() => {")
        first_effect_read = source.index("  useEffect(() => {\n    const refreshIfStale = () =>")
        self.assertLess(
            declaration,
            first_effect_read,
            "editableSettings must be initialized before useEffect dependency arrays evaluate it during render",
        )


    def test_plugin_compiles_as_a_real_es_module(self):
        # String contracts and `node --check` both missed a doubled closing
        # brace that made the whole renderer module unloadable in Electron;
        # compile the module for real. SyntaxError fails the gate; any other
        # failure (the plugin SDK cannot resolve outside Electron) proves the
        # parse itself succeeded.
        probe = (
            "import(" + repr("file://" + PLUGIN.as_posix()) + ")"
            ".then(() => process.exit(0))"
            ".catch((error) => process.exit(error && error.name === 'SyntaxError' ? 1 : 2))"
        )
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertIn(
            result.returncode,
            (0, 2),
            msg=(result.stderr or result.stdout)[-800:],
        )


if __name__ == "__main__":
    unittest.main()

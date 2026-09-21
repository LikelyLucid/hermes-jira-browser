from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[1] / "desktop" / "plugin.js"


class DesktopPluginTests(unittest.TestCase):
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

    def test_ticket_title_uses_the_full_drawer_row(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn(
            "className: 'min-w-0 basis-full text-base font-semibold leading-snug text-foreground'",
            source,
        )

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
        self.assertIn("How Jira looks", source)
        self.assertIn("Ticket layout", source)
        self.assertIn("Scan a backlog or queue", source)
        self.assertIn("Advanced settings", source)
        self.assertIn("$HERMES_HOME/jira-browser/settings.json", source)
        self.assertIn("const updateSettingsField = useCallback", source)
        self.assertIn("const updateSavedView = useCallback", source)
        self.assertIn("onAddBacklogView: addBacklogView", source)
        self.assertIn("onCopyPath: copySettingsPath", source)
        self.assertIn("style: { width: `${drawerWidth}px` }", source)

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
        self.assertIn("const listGrid = compact ? 'grid-cols-[minmax(14rem,1fr)_6rem_5rem_8rem_5rem]'", source)
        self.assertIn("compact ? 'min-w-[42rem] space-y-0' : 'min-w-[52rem] space-y-1.5'", source)
        self.assertIn("compact ? 'gap-2 border-b border-l-2 border-(--ui-stroke-tertiary) px-2 py-1'", source)
        self.assertIn("compact ? 'truncate text-[0.68rem] text-(--ui-text-secondary)'", source)

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

    def test_working_filter_reacts_to_live_sessions_and_ranks_lowest_last(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("if (/lowest/.test(label)) return 4\n  if (/low/.test(label)) return 3", source)
        self.assertIn("quickFilter, workingSessionIds, workStates])", source)

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
        self.assertIn("const working = linkedWork.some", source)
        self.assertIn("ring-1 ring-(--dt-composer-ring)", source)
        self.assertIn("children: 'Working'", source)

    def test_existing_linked_work_is_resumed_without_duplicate_session(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const resumeWork = useCallback", source)
        self.assertIn("Resume work", source)
        self.assertIn("onClick: resumeWork", source)
        self.assertIn("onClick: startWork", source)
        self.assertIn("children: busyAction === 'work' ? 'Creating…' : resumableLink ? 'New chat' : 'Open work session'", source)

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
        self.assertIn("Auto-link chats from worktree", source)
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
        self.assertIn("if (alive && !isConfirmedTransientRpcFailure(cause)) setWorkingSessionIds(new Set())", refresh)
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


if __name__ == "__main__":
    unittest.main()

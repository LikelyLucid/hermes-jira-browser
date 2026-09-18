from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[1] / "desktop" / "plugin.js"


class DesktopPluginTests(unittest.TestCase):
    def test_ticket_lists_use_persistent_stale_while_revalidate_cache(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const ISSUE_CACHE_KEY = 'issue-list-cache-v1'", source)
        self.assertIn("pluginContext.storage.get(ISSUE_CACHE_KEY", source)
        self.assertIn("pluginContext.storage.set(ISSUE_CACHE_KEY", source)
        self.assertIn("Cached · refreshing…", source)

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
        self.assertIn("Human-friendly settings", source)
        self.assertIn("Agent JSON", source)
        self.assertIn("$HERMES_HOME/jira-browser/settings.json", source)
        self.assertIn("const updateSettingsField = useCallback", source)
        self.assertIn("const updateSavedView = useCallback", source)
        self.assertIn("onCopyPath: copySettingsPath", source)
        self.assertIn("style: { width: `${drawerWidth}px` }", source)

    def test_workflow_lanes_are_auto_detected_and_cached_from_jira(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const LANE_CACHE_KEY = 'workflow-lane-cache-v1'", source)
        self.assertIn("const [detectedLanes, setDetectedLanes]", source)
        self.assertIn("Promise.all(laneProbeKeys.map", source)
        self.assertIn("/transitions`)", source)
        self.assertIn("writeLaneCache(projectKeys, lanes)", source)
        self.assertIn("for (const lane of detectedLanes)", source)

    def test_cards_show_cached_linked_work_state(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const WORK_STATE_CACHE_KEY = 'ticket-work-state-cache-v1'", source)
        self.assertIn("const [workStates, setWorkStates]", source)
        self.assertIn("api(`/links/${encodeURIComponent(issue.id)}`)", source)
        self.assertIn("workState: workStates[issue.key]", source)
        self.assertIn("Linked work", source)

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
        self.assertIn("await host.openSession(storedId, { awaitHydration: true, expectHistory: true, forceResume: true })", source)
        self.assertIn("await host.openSession(link.session_id, { awaitHydration: true, expectHistory: true, forceResume: true })", source)
        self.assertNotIn("expectHistory: false", source)
        start_work = source[source.index("const startWork = useCallback"):source.index("if (!issue) return")]
        self.assertNotIn("if (resumableLink)", start_work)
        self.assertIn("linkedWorktree?.path", start_work)
        self.assertNotIn("await onLinksChanged()", start_work)
        open_call = "await host.openSession(storedId, { awaitHydration: true, expectHistory: true, forceResume: true })"
        self.assertLess(start_work.index(open_call), start_work.index("void onLinksChanged()"))
        self.assertIn("traceWorkOpen(issue.key, 'session-created')", start_work)
        self.assertIn("traceWorkOpen(issue.key, 'open-complete')", start_work)

    def test_related_chats_are_discovered_and_attachable(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("host.listPersistedSessions(null, { profile, limit: 500 })", source)
        self.assertIn("host.request('projects.project_sessions'", source)
        self.assertIn("session_limit: 20_000", source)
        self.assertIn("api(`/sessions/${encodeURIComponent(jiraProjectKey)}`)", source)
        self.assertIn("Likely related chats", source)
        self.assertIn("const attachRelatedChat = useCallback", source)
        self.assertIn("Scan chats", source)

    def test_linked_chats_can_be_unlinked_without_deleting_the_chat(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const DETACHED_CHAT_LINKS_KEY = 'detached-ticket-chat-links-v1'", source)
        self.assertIn("const unlinkChat = useCallback", source)
        self.assertIn("method: 'DELETE'", source)
        self.assertIn("onUnlink: unlinkChat", source)
        self.assertIn("name: 'link-break'", source)
        self.assertIn("!detachedIds.has(sessionId)", source)

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
        self.assertIn("host.request('prompt.submit'", source)
        self.assertIn("Do not mutate Jira", source)
        self.assertIn("Draft update", source)

    def test_transition_suggestion_requires_explicit_click(self):
        source = PLUGIN.read_text(encoding="utf-8")

        self.assertIn("const suggestedTransition = useMemo", source)
        self.assertIn("Suggested next step", source)
        self.assertIn("Apply suggestion", source)

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
        self.assertNotIn("host.openWorkspace", source)
        self.assertNotIn("area: 'panes'", source)


if __name__ == "__main__":
    unittest.main()

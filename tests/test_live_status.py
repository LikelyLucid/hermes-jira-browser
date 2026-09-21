from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[1] / "desktop" / "plugin.js"


class LiveStatusTests(unittest.TestCase):
    def _source(self):
        return PLUGIN.read_text(encoding="utf-8")

    def test_shipped_entry_contains_canonical_states_and_event_mapping(self):
        source = self._source()
        for state in ("working", "waiting", "starting", "idle", "failed", "archived"):
            self.assertIn(state, source)
        self.assertIn("function liveEventState", source)
        self.assertIn("raw.includes('complete')", source)
        self.assertIn("raw.includes('finish')", source)
        self.assertIn("const state = liveEventState(raw)", source)

    def test_live_surface_uses_native_optional_sdk_surfaces(self):
        source = self._source()
        self.assertIn("COMPOSER_AREAS", source)
        self.assertIn("PALETTE_AREA", source)
        self.assertIn("typeof host.requestProfile === 'function'", source)
        self.assertIn("host.profileRoutes()", source)
        self.assertIn("host.listPersistedSessions(route", source)
        self.assertIn("function installLiveStatus", source)

    def test_live_status_notifications_are_owner_scoped_and_deduplicated(self):
        source = self._source()
        self.assertIn("LIVE_STATUS_NOTIFICATIONS_KEY = 'live-status-notifications-v1'", source)
        self.assertIn("LIVE_STATUS_RECEIPTS_KEY = 'live-status-notification-receipts-v1'", source)
        self.assertIn("function notifyLiveTransition", source)
        self.assertIn("liveEntryKey(current)", source)
        self.assertIn("pluginContext?.os?.notify", source)
        self.assertIn("const targetProfile = String(link?.target_profile || link?.targetProfile || '').trim()", source)

    def test_live_registration_provides_palette_keybind_status_and_titlebar(self):
        source = self._source()
        self.assertIn("id: 'toggle-live-notifications'", source)
        self.assertIn("id: 'live-open-jira-keybind'", source)
        self.assertIn("id: 'live-status'", source)
        self.assertIn("id: 'live-status-titlebar'", source)
        self.assertIn("defaults: ['mod+shift+j']", source)
        self.assertIn("host.navigate(ROUTE)", source)

    def test_ticket_state_aggregation_preserves_highest_priority(self):
        source = self._source()
        self.assertIn("function liveStatusPriority", source)
        self.assertIn("if (String(entry?.ownerKey || '') !== activeOwnerKey) continue", source)
        self.assertIn("liveStatusPriority(entry.state) > liveStatusPriority(current)", source)

    def test_issue_batch_client_rejects_invalid_keys_instead_of_silently_dropping(self):
        source = self._source()
        self.assertIn("const rawKeys =", source)
        self.assertIn("if (rawKeys.some(key => !ISSUE_KEY_PATTERN.test(key))) throw new Error", source)
        self.assertIn("async function invalidateIssueBatchCache(owner, origin = '')", source)
        self.assertIn("await invalidateIssueBatchCache(owner, status?.base_url)", source)

    def test_jira_page_publishes_selected_and_focused_link_context(self):
        source = self._source()
        self.assertIn("publishJiraContext({", source)
        self.assertIn("subscribeLiveStatuses", source)
        self.assertIn("entry.state === 'working'", source)
        self.assertIn("installLiveStatus(ctx)", source)
        self.assertIn("action: 'jira-browser.open'", source)
        self.assertIn("COMPOSER_AREAS?.attachments", source)
        self.assertIn("Insert Jira comment into composer", source)
        self.assertIn("[Untrusted Jira comment from", source)
        self.assertIn("LIVE_STATUS_RECEIPTS_KEY", source)
        self.assertIn("function notifyLiveTransition", source)
        self.assertIn("pluginContext?.os?.notify", source)


if __name__ == "__main__":
    unittest.main()

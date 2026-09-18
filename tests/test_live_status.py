from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]
LIVE = ROOT / "desktop" / "live_status.js"
CORE = ROOT / "desktop" / "live_status_core.mjs"
CORE_TEST = ROOT / "desktop" / "live_status_core.test.mjs"


class LiveStatusTests(unittest.TestCase):
    def test_core_status_contract_executes(self):
        result = subprocess.run(
            ["node", str(CORE_TEST)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_surface_uses_native_optional_sdk_surfaces(self):
        source = LIVE.read_text(encoding="utf-8")
        self.assertIn("STATUSBAR_AREAS", source)
        self.assertIn("TITLEBAR_AREAS", source)
        self.assertIn("PALETTE_AREA", source)
        self.assertIn("KEYBINDS_AREA", source)
        self.assertIn("SessionStatusDot", source)
        self.assertIn("typeof host.requestProfile === 'function'", source)
        self.assertIn("host.profileRoutes()", source)
        self.assertIn("host.listPersistedSessions(route", source)

    def test_live_status_has_canonical_states_and_owner_scoped_notifications(self):
        core = (ROOT / "desktop" / "live_status_core.mjs").read_text(encoding="utf-8")
        source = LIVE.read_text(encoding="utf-8")
        for state in ("working", "waiting", "starting", "idle", "failed", "archived"):
            self.assertIn(state, core)
        self.assertIn("LIVE_STATUS_NOTIFICATIONS_KEY = 'live-status-notifications-v1'", source)
        self.assertIn("pluginContext?.storage?.get", source)
        self.assertIn("pluginContext?.storage?.set", source)
        self.assertIn("notificationTransition", source)
        self.assertIn("${entry.ownerKey}:${linkId(entry.link)}:${entry.epoch}", source)
        self.assertIn("transition.kind === 'needs-input' ? 'warning' : 'success'", source)
        self.assertNotIn("host.request('session.active_list'", source)
        self.assertIn("available: Boolean(owner) && !ambiguous.has(id)", source)
        self.assertIn("eventStateForSession(eventStates", source)

    def test_live_registration_provides_route_palette_keybind_status_and_titlebar(self):
        source = LIVE.read_text(encoding="utf-8")
        self.assertIn("id: 'toggle-notifications'", source)
        self.assertIn("id: 'open-jira-keybind'", source)
        self.assertIn("id: 'jira-browser.open'", source)
        self.assertIn("defaults: ['mod+shift+j']", source)
        self.assertIn("render: () => jsx(LiveStatusBar, {})", source)
        self.assertIn("render: () => jsx(LiveTitlebar, {})", source)
        self.assertIn("host.navigate('/jira')", source)

    def test_jira_page_publishes_selected_and_focused_link_context(self):
        source = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("publishJiraContext({", source)
        self.assertIn("subscribeLiveStatuses", source)
        self.assertIn("entry.state === 'working'", source)
        self.assertIn("installLiveStatus(ctx)", source)
        self.assertIn("action: 'jira-browser.open'", source)


if __name__ == "__main__":
    unittest.main()

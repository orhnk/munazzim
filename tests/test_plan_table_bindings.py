from __future__ import annotations

import unittest

try:
    from munazzim.tui.app import PlanTable
    TEXTUAL_AVAILABLE = True
except Exception:  # pragma: no cover - silent fallback while running tests in CI-less env
    PlanTable = None  # type: ignore[assignment]
    TEXTUAL_AVAILABLE = False


@unittest.skipUnless(TEXTUAL_AVAILABLE, "textual not installed")
class PlanTableBindingsTest(unittest.TestCase):
    def test_plan_table_exposes_navigation_bindings(self):
        """PlanTable should expose textual bindings for navigation and not
        block the hjkl/arrow/g/G keys so users can use Textual's key API.
        """
        # 'g' should now be registered as a binding on the PlanTable
        self.assertTrue(any(getattr(b, 'key', None) == 'g' for b in PlanTable.BINDINGS))
        # Verify 'hjkl' and arrow keys are not included in the BLOCKED_KEYS set
        blocked = getattr(PlanTable, 'BLOCKED_KEYS', set())
        for k in ['g', 'G', 'h', 'j', 'k', 'l', 'up', 'down', 'left', 'right']:
            self.assertNotIn(k, blocked)

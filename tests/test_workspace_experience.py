from pathlib import Path
import re
import shutil
import subprocess
import unittest


DASHBOARD = Path(__file__).resolve().parents[1] / "src/trace_memory/api/dashboard.html"


@unittest.skipUnless(shutil.which("node"), "Node is required for dashboard JS tests")
class TestWorkspaceExperience(unittest.TestCase):
    def run_js(self, source: str) -> None:
        result = subprocess.run(["node", "-"], input=source, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dashboard_script_parses(self) -> None:
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", DASHBOARD.read_text(), re.S)
        self.assertTrue(scripts)
        result = subprocess.run(["node", "--check", "-"], input="\n".join(scripts), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_today_workboard_respects_beijing_dates_and_available_tasks(self) -> None:
        html = DASHBOARD.read_text()
        planning = html[html.index("function todayTaskPlan"):html.index("function renderTodayWorkboard")]
        dates = html[html.index("function hhmmToMinutes"):html.index("function addMinutesToYmdHhmm")]
        self.run_js(dates + planning + """
          const assert = require('node:assert/strict');
          const now = Date.parse('2026-11-22T17:00:00Z');
          const task = (title, payload) => ({ title, payload: { task_status: 'todo', ...payload } });
          const late = task('late', { due_at: '2026-11-22', is_overdue: true });
          const due = task('due', { due_at: '2026-11-23', priority_score: 3 });
          const morning = task('morning', { start_at: '2026-11-23T09:00:00+08:00', priority_score: 99 });
          const noon = task('noon', { start_at: '2026-11-23T12:00:00+08:00' });
          const done = task('done', { task_status: 'done', due_at: '2026-11-23', priority_score: 100 });
          const plan = todayTaskPlan([noon, done, morning, due, late], now);
          assert.equal(plan.today, '2026-11-23');
          assert.equal(plan.next, late);
          assert.deepEqual(plan.dueToday, [due]);
          assert.deepEqual(plan.scheduledToday, [morning, noon]);
          assert.equal(todayTaskPlan([morning, due, done], now).next, due);
          assert.equal(todayTaskPlan([morning], now).next, undefined);
        """)

    def test_focus_timer_counts_elapsed_time_and_excludes_pauses(self) -> None:
        html = DASHBOARD.read_text()
        timer = html[html.index("function syncFocusElapsed"):html.index("function openFocusLock")]
        self.run_js(timer + """
          const assert = require('node:assert/strict');
          const state = { mode: 'pomodoro', runningSince: 1000, accumulatedMs: 500, totalSeconds: 60 };
          syncFocusElapsed(state, 21500);
          assert.equal(state.elapsedSeconds, 21);
          assert.equal(state.remainingSeconds, 39);
          Object.assign(state, { paused: true, accumulatedMs: 21000 });
          syncFocusElapsed(state, 86500);
          assert.equal(state.elapsedSeconds, 21);
          Object.assign(state, { paused: false, runningSince: 86500 });
          syncFocusElapsed(state, 146500);
          assert.equal(state.elapsedSeconds, 81);
          assert.equal(state.remainingSeconds, 0);
        """)

"""Tests for project_bridge.py. Run: python3 -m unittest discover -s scripts"""

import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import project_bridge as pb  # noqa: E402


class ParseCommand(unittest.TestCase):
    def test_known_commands(self):
        self.assertEqual(pb.parse_command("cmd:status-now"), ("Status", "Now"))
        self.assertEqual(pb.parse_command("cmd:verify-needs-windows"), ("Verification", "Needs Windows"))
        self.assertEqual(pb.parse_command("cmd:verify-needs-macos"), ("Verification", "Needs macOS"))
        self.assertEqual(pb.parse_command("cmd:verify-needs-android"), ("Verification", "Needs Android device"))
        self.assertEqual(pb.parse_command("cmd:verify-needs-iphone"), ("Verification", "Needs iPhone"))

    def test_needs_kade_is_gone(self):
        # A decision is an assignee, not a Verification value; the old label is reported, not applied.
        self.assertIsNone(pb.parse_command("cmd:verify-needs-kade"))
        self.assertNotIn("Needs Kade", pb.VERIFICATION_OPTIONS)
        plan = pb.plan_labels("labeled", "cmd:verify-needs-kade", [], True)
        self.assertEqual((plan.apply, plan.unknown), ([], ["cmd:verify-needs-kade"]))

    def test_phase_is_gone(self):
        # The Project has no Phase field; a phase label is reported, not applied.
        self.assertIsNone(pb.parse_command("cmd:phase-p0"))
        self.assertNotIn("Phase", {field for field, _ in pb.COMMANDS.values()})
        plan = pb.plan_labels("labeled", "cmd:phase-p0", [], True)
        self.assertEqual((plan.apply, plan.unknown), ([], ["cmd:phase-p0"]))

    def test_verification_environments_are_the_needs_options(self):
        self.assertEqual(
            pb.VERIFICATION_ENVIRONMENTS,
            ["Needs Windows", "Needs macOS", "Needs Android device", "Needs iPhone"],
        )
        for option in pb.VERIFICATION_ENVIRONMENTS:
            self.assertIn(option, pb.VERIFICATION_OPTIONS)

    def test_case_and_space_insensitive(self):
        self.assertEqual(pb.parse_command("CMD:Status-Now "), ("Status", "Now"))

    def test_unknown_and_non_command(self):
        self.assertIsNone(pb.parse_command("cmd:status-later"))
        self.assertIsNone(pb.parse_command("bug"))

    def test_every_command_names_a_real_option(self):
        options = {
            "Status": pb.STATUS_OPTIONS,
            "Verification": pb.VERIFICATION_OPTIONS,
            "Priority": pb.PRIORITY_OPTIONS,
        }
        for suffix, (field, option) in pb.COMMANDS.items():
            self.assertIn(option, options[field], suffix)


class PlanLabels(unittest.TestCase):
    def test_opened_by_writer_applies_all_commands(self):
        plan = pb.plan_labels("opened", "", ["bug", "cmd:status-now", "cmd:priority-p1"], True)
        self.assertEqual(plan.apply, ["cmd:status-now", "cmd:priority-p1"])
        self.assertEqual(plan.strip, [])

    def test_opened_by_outsider_strips_commands(self):
        plan = pb.plan_labels("opened", "", ["cmd:status-now", "question"], False)
        self.assertEqual(plan.apply, [])
        self.assertEqual(plan.strip, ["cmd:status-now"])

    def test_labeled_only_counts_the_new_label(self):
        plan = pb.plan_labels("labeled", "cmd:verify-verified", ["cmd:status-now", "cmd:verify-verified"], True)
        self.assertEqual(plan.apply, ["cmd:verify-verified"])

    def test_labeled_with_ordinary_label_does_nothing(self):
        plan = pb.plan_labels("labeled", "bug", ["bug"], True)
        self.assertEqual((plan.apply, plan.strip, plan.unknown), ([], [], []))

    def test_unknown_command_is_reported_not_removed(self):
        plan = pb.plan_labels("labeled", "cmd:status-someday", [], True)
        self.assertEqual(plan.unknown, ["cmd:status-someday"])
        self.assertEqual(plan.apply, [])

    def test_other_actions_do_nothing(self):
        plan = pb.plan_labels("edited", "", ["cmd:status-now"], True)
        self.assertEqual(plan.apply, [])

    def test_last_label_wins_per_field(self):
        self.assertEqual(
            pb.last_wins(["cmd:status-next", "cmd:priority-p2", "cmd:status-now"]),
            {"Status": "Now", "Priority": "P2"},
        )


PROJECT_DATA = {
    "organization": {
        "projectV2": {
            "id": "PVT_1",
            "fields": {
                "nodes": [
                    {"id": "F_status", "name": "Status", "options": [{"id": "o_now", "name": "Now"}]},
                    {"id": "F_target", "name": "Target"},
                    {},
                ]
            },
        }
    }
}


class ProjectFields(unittest.TestCase):
    def test_option_lookup(self):
        project = pb.parse_project(PROJECT_DATA)
        self.assertEqual(project.option("Status", "Now"), ("F_status", "o_now"))

    def test_missing_field_or_option_is_an_error(self):
        project = pb.parse_project(PROJECT_DATA)
        with self.assertRaises(pb.GitHubError):
            project.option("Priority", "P1")
        with self.assertRaises(pb.GitHubError):
            project.option("Status", "Backlog")

    def test_missing_project_is_an_error(self):
        with self.assertRaises(pb.GitHubError):
            pb.parse_project({"organization": {"projectV2": None}})


def node(kind, number, title, state, values, repo="resonance-stream", archived=False, assignees=()):
    content = {"__typename": kind, "title": title}
    if kind != "DraftIssue":
        content.update(
            number=number, url=f"https://github.com/star-resonance/{repo}/issues/{number}",
            state=state, repository={"name": repo},
            assignees={"nodes": [{"login": login} for login in assignees]},
        )
    field_nodes = []
    for name, value in values.items():
        if name in ("Start", "Target"):
            field_nodes.append({"date": value, "field": {"name": name}})
        else:
            field_nodes.append({"name": value, "field": {"name": name}})
    field_nodes.append({})  # text/number values come back as empty objects
    return {"isArchived": archived, "fieldValues": {"nodes": field_nodes}, "content": content}


def page(*nodes):
    return {"organization": {"projectV2": {"title": "Resonance", "items": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(nodes)}}}}


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.items = pb.parse_items([page(
            node("Issue", 1, "Capture runs on Windows", "OPEN",
                 {"Status": "Now", "Priority": "P1", "Target": "2026-10-20"}),
            node("Issue", 2, "Fixed-point math | module", "OPEN",
                 {"Status": "Next"}, repo="resonance-lab"),
            node("Issue", 3, "Export works on device", "CLOSED",
                 {"Status": "Done", "Verification": "Needs Windows"},
                 assignees=["enjay27"]),
            node("PullRequest", 4, "Bus recorder", "MERGED",
                 {"Status": "Done", "Verification": "NOT VERIFIED"}),
            node("DraftIssue", None, "Look at audio later", None, {}),
            node("Issue", 5, "Old thing", "CLOSED", {"Status": "Done"}, archived=True),
            node("Issue", 6, "Choose the sound engine", "OPEN", {"Status": "Next"},
                 assignees=["enjay27"]),
            node("Issue", 7, "Build on an Apple silicon Mac", "CLOSED",
                 {"Status": "Done", "Verification": "Needs macOS"}, repo="resonance-lab"),
        )])

    def test_archived_items_are_skipped(self):
        self.assertEqual(len(self.items), 7)

    def test_open_means_open_or_draft_and_not_done(self):
        open_refs = sorted(i.ref() for i in self.items if i.is_open)
        self.assertEqual(open_refs, ["draft", "resonance-lab#2", "resonance-stream#1", "resonance-stream#6"])

    def test_assignees_are_read(self):
        by_ref = {i.ref(): i.assignees for i in self.items}
        self.assertEqual(by_ref["resonance-stream#6"], ["enjay27"])
        self.assertEqual(by_ref["resonance-stream#1"], [])
        self.assertEqual(by_ref["draft"], [])

    def test_items_query_asks_for_assignees(self):
        self.assertIn("assignees", pb.ITEMS_QUERY)

    def test_render(self):
        when = dt.datetime(2026, 10, 8, 0, 17, tzinfo=dt.timezone.utc)
        text = pb.render_status(self.items, "Resonance", when, "https://github.com/orgs/star-resonance/projects/1")
        self.assertIn("Generated 2026-10-08 00:17 UTC (2026-10-08 09:17 KST)", text)
        self.assertIn("## Now (1)", text)
        self.assertIn("- [resonance-stream#1](https://github.com/star-resonance/resonance-stream/issues/1) Capture runs on Windows · P1 · target 2026-10-20", text)
        # a decision is an open item assigned to the maintainer; a closed one is not waiting
        self.assertIn("## Waiting on Kade (1)", text)
        self.assertIn("resonance-stream#6", text.split("## Waiting on Kade")[1].split("##")[0])
        # closed but still waiting for a check on one machine or device
        self.assertIn("## Needs Windows (1)", text)
        self.assertIn("resonance-stream#3", text.split("## Needs Windows")[1].split("##")[0])
        self.assertIn("## Needs macOS (1)", text)
        self.assertIn("resonance-lab#7", text.split("## Needs macOS")[1].split("##")[0])
        self.assertIn("## Needs Android device (0)", text)
        self.assertIn("## Needs iPhone (0)", text)
        self.assertNotIn("Needs Kade", text)
        self.assertIn("## NOT VERIFIED (1)", text)
        # the Project has no Phase field, so there is no roadmap by phase
        self.assertNotIn("Roadmap", text)
        self.assertNotIn("phase", text.lower())
        # pipes in titles cannot break Markdown tables or lists
        self.assertIn("Fixed-point math / module", text)
        # the draft has no status, so it is backlog
        self.assertIn("## Backlog (1)", text)
        self.assertIn("- draft Look at audio later", text)

    def test_start_is_shown_before_target(self):
        items = pb.parse_items([page(node("Issue", 9, "Dated work", "OPEN",
                                          {"Status": "Next", "Start": "2026-10-25", "Target": "2026-11-01"}))])
        text = pb.render_status(items, "Resonance", dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc))
        self.assertIn("Dated work · start 2026-10-25 · target 2026-11-01", text)

    def test_empty_project_renders(self):
        text = pb.render_status([], "Resonance", dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc))
        self.assertIn("## Now (0)", text)
        self.assertIn("## Waiting on Kade (0)", text)
        self.assertIn("- none", text)


class SyncCommand(unittest.TestCase):
    """cmd_sync end to end, with GitHub replaced by a recorder."""

    def run_sync(self, env, writer=True):
        calls = []

        class FakeGitHub:
            def __init__(self, token):
                self.token = token

            def graphql(self, query, variables):
                calls.append(("graphql", query.split("(")[0].split()[-1], variables))
                if "addProjectV2ItemById" in query:
                    return {"addProjectV2ItemById": {"item": {"id": "ITEM_1"}}}
                if "updateProjectV2ItemFieldValue" in query:
                    return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "ITEM_1"}}}
                return {"organization": {"projectV2": {"id": "PVT_1", "fields": {"nodes": [
                    {"id": "F_s", "name": "Status", "options": [{"id": "o_now", "name": "Now"}]},
                    {"id": "F_p", "name": "Priority", "options": [{"id": "o_p1", "name": "P1"}]},
                ]}}}}

            def rest(self, method, path, body=None):
                calls.append((method, path))
                if path.endswith("/permission"):
                    return {"permission": "write" if writer else "read", "role_name": "write" if writer else "read"}
                return None

        base = {
            "REPO": "star-resonance/resonance-stream", "ISSUE_NUMBER": "7", "ISSUE_NODE_ID": "I_7", "ORG": "star-resonance",
            "PROJECT_NUMBER": "1", "PROJECT_TOKEN": "p", "GITHUB_TOKEN": "g", "SENDER": "enjay27",
        }
        base.update(env)
        with mock.patch.object(pb, "GitHub", FakeGitHub), mock.patch.dict(os.environ, base, clear=True), \
                mock.patch("builtins.print"):
            code = pb.cmd_sync(None)
        return code, calls

    def test_opened_with_commands_sets_fields_then_removes_labels(self):
        code, calls = self.run_sync({
            "EVENT_ACTION": "opened",
            "ISSUE_LABELS": json.dumps(["cmd:status-now", "cmd:priority-p1", "bug"]),
        })
        self.assertEqual(code, 0)
        sets = [c[2]["option"] for c in calls if c[0] == "graphql" and "option" in c[2]]
        self.assertEqual(sorted(sets), ["o_now", "o_p1"])
        deletes = [c[1] for c in calls if c[0] == "DELETE"]
        self.assertEqual(deletes, [
            "/repos/star-resonance/resonance-stream/issues/7/labels/cmd%3Astatus-now",
            "/repos/star-resonance/resonance-stream/issues/7/labels/cmd%3Apriority-p1",
        ])
        # field updates happen before any label is removed
        first_delete = next(i for i, c in enumerate(calls) if c[0] == "DELETE")
        last_set = max(i for i, c in enumerate(calls) if c[0] == "graphql" and "option" in c[2])
        self.assertLess(last_set, first_delete)

    def test_outsider_is_added_but_commands_are_stripped(self):
        code, calls = self.run_sync({
            "EVENT_ACTION": "opened", "ISSUE_LABELS": json.dumps(["cmd:status-now"]),
        }, writer=False)
        self.assertEqual(code, 0)
        self.assertFalse([c for c in calls if c[0] == "graphql" and "option" in c[2]])
        self.assertTrue([c for c in calls if c[0] == "graphql" and c[2].get("content") == "I_7"])
        self.assertEqual([c[1] for c in calls if c[0] == "DELETE"],
                         ["/repos/star-resonance/resonance-stream/issues/7/labels/cmd%3Astatus-now"])


class DateRequest(unittest.TestCase):
    def test_start_and_target_take_a_calendar_date(self):
        self.assertEqual(pb.parse_date_request("Target", "2026-11-01"), ("Target", "2026-11-01"))
        self.assertEqual(pb.parse_date_request("start", " 2026-02-28 "), ("Start", "2026-02-28"))

    def test_clear_removes_the_date(self):
        self.assertEqual(pb.parse_date_request("Target", "clear"), ("Target", None))

    def test_other_fields_are_refused(self):
        # single-select fields are set with cmd: labels; dates are the only gap
        for name in ("Status", "Priority", "Verification", "Title", ""):
            with self.assertRaises(pb.RequestError):
                pb.parse_date_request(name, "2026-11-01")

    def test_only_yyyy_mm_dd_of_a_real_day(self):
        for value in ("2026-13-01", "2026-02-30", "20261101", "2026-11-1", "next week", ""):
            with self.assertRaises(pb.RequestError, msg=value):
                pb.parse_date_request("Target", value)

    def test_the_refusal_does_not_echo_the_value(self):
        # a value is written by a person; it never reaches the public log
        with self.assertRaises(pb.RequestError) as ctx:
            pb.parse_date_request("Target", "::warning::hi")
        self.assertNotIn("::warning::", str(ctx.exception))

    def test_date_field_id(self):
        project = pb.parse_project(PROJECT_DATA)
        self.assertEqual(project.field_id("Target"), "F_target")
        with self.assertRaises(pb.GitHubError):
            project.field_id("Start")


class SetDateCommand(unittest.TestCase):
    """cmd_set_date end to end, with GitHub replaced by a recorder."""

    def run_set_date(self, field, date, number="7"):
        calls, printed = [], []

        class FakeGitHub:
            def __init__(self, token):
                self.token = token

            def graphql(self, query, variables):
                calls.append(("graphql", query.split("(")[0].split()[-1], variables))
                if "addProjectV2ItemById" in query:
                    return {"addProjectV2ItemById": {"item": {"id": "ITEM_1"}}}
                if "Value(" in query:
                    return {}
                return {"organization": {"projectV2": {"id": "PVT_1", "fields": {"nodes": [
                    {"id": "F_start", "name": "Start"}, {"id": "F_target", "name": "Target"},
                ]}}}}

            def rest(self, method, path, body=None):
                calls.append((method, path))
                return {"node_id": "I_7", "title": "A title that stays out of the log"}

        env = {
            "REPO": "star-resonance/resonance-stream", "ISSUE_NUMBER": number, "FIELD": field, "DATE": date,
            "ORG": "star-resonance", "PROJECT_NUMBER": "1", "PROJECT_TOKEN": "p", "GITHUB_TOKEN": "g",
        }
        with mock.patch.object(pb, "GitHub", FakeGitHub), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            code = pb.main(["set-date"])
        return code, calls, "\n".join(printed)

    def test_sets_the_date_on_the_callers_issue(self):
        code, calls, out = self.run_set_date("Target", "2026-11-01")
        self.assertEqual(code, 0)
        # the issue is read from the calling repository, so no other repository's issue can be named
        self.assertIn(("GET", "/repos/star-resonance/resonance-stream/issues/7"), calls)
        self.assertTrue([c for c in calls if c[0] == "graphql" and c[2].get("content") == "I_7"])
        sets = [c[2] for c in calls if c[0] == "graphql" and "date" in c[2]]
        self.assertEqual(sets, [{"project": "PVT_1", "item": "ITEM_1", "field": "F_target", "date": "2026-11-01"}])
        self.assertIn("set Target = 2026-11-01", out)
        self.assertNotIn("A title", out)

    def test_clear(self):
        code, calls, out = self.run_set_date("Start", "clear")
        self.assertEqual(code, 0)
        clears = [c for c in calls if c[0] == "graphql" and c[1] == "mutation" and "date" not in c[2]
                  and c[2].get("field") == "F_start"]
        self.assertEqual(len(clears), 1)
        self.assertIn("cleared Start", out)

    def test_bad_request_makes_no_call(self):
        for field, date, number in (("Target", "soon", "7"), ("Status", "2026-11-01", "7"),
                                    ("Target", "2026-11-01", "7/../../x")):
            code, calls, _ = self.run_set_date(field, date, number)
            self.assertEqual(code, 1, (field, date, number))
            self.assertEqual(calls, [], (field, date, number))


if __name__ == "__main__":
    unittest.main()

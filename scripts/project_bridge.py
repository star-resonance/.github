#!/usr/bin/env python3
"""Bridge between issues and the organization Project.

Three commands:

  sync      Run by the project-sync workflow on an issue event. Adds the issue to the
            Project and applies command labels (cmd:<field>-<value>) to Project fields,
            then removes those labels. Labels are requests, never state.
  set-date  Run by the project-set workflow (workflow_dispatch): sets or clears an issue's
            Start or Target date, which no label can carry.
  snapshot  Exports the Project to STATUS.md, which cloud sessions read because they
            cannot reach the Project API themselves.

Both talk to GitHub with plain HTTPS (no third-party packages), so the same file runs in
Actions and on a developer machine:

  GH_TOKEN=$(gh auth token) python3 project_bridge.py snapshot \
      --org star-resonance --project 1 --out STATUS.md

Nothing here prints issue titles to the log: the sync job runs in public repositories,
whose Actions logs are public.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"
PREFIX = "cmd:"
WRITE_ROLES = {"admin", "maintain", "write"}

# Field and option names must match the Project exactly (see the setup runbook).
FIELD_STATUS = "Status"
FIELD_VERIFICATION = "Verification"
FIELD_PRIORITY = "Priority"
FIELD_START = "Start"
FIELD_TARGET = "Target"
DATE_FIELDS = [FIELD_START, FIELD_TARGET]
CLEAR = "clear"

STATUS_OPTIONS = ["Backlog", "Next", "Now", "In review", "Done"]
# A check that cannot run in a cloud session or in CI waits for a machine or a device. A
# decision is not a verification: it is an issue assigned to a maintainer (MAINTAINERS).
VERIFICATION_ENVIRONMENTS = ["Needs Windows", "Needs macOS", "Needs Android device", "Needs iPhone"]
VERIFICATION_OPTIONS = ["Verified", "NOT VERIFIED"] + VERIFICATION_ENVIRONMENTS
MAINTAINERS = {"enjay27"}
PRIORITY_OPTIONS = ["P1", "P2", "P3"]

# cmd:<suffix>  ->  (field, option)
COMMANDS: dict[str, tuple[str, str]] = {
    "status-backlog": (FIELD_STATUS, "Backlog"),
    "status-next": (FIELD_STATUS, "Next"),
    "status-now": (FIELD_STATUS, "Now"),
    "status-in-review": (FIELD_STATUS, "In review"),
    "status-done": (FIELD_STATUS, "Done"),
    "verify-verified": (FIELD_VERIFICATION, "Verified"),
    "verify-not-verified": (FIELD_VERIFICATION, "NOT VERIFIED"),
    "verify-needs-windows": (FIELD_VERIFICATION, "Needs Windows"),
    "verify-needs-macos": (FIELD_VERIFICATION, "Needs macOS"),
    "verify-needs-android": (FIELD_VERIFICATION, "Needs Android device"),
    "verify-needs-iphone": (FIELD_VERIFICATION, "Needs iPhone"),
    "priority-p1": (FIELD_PRIORITY, "P1"),
    "priority-p2": (FIELD_PRIORITY, "P2"),
    "priority-p3": (FIELD_PRIORITY, "P3"),
}


# --------------------------------------------------------------------------- decisions


def parse_command(label: str) -> tuple[str, str] | None:
    """Return (field, option) for a known command label, else None."""
    if not label.lower().startswith(PREFIX):
        return None
    return COMMANDS.get(label[len(PREFIX):].strip().lower())


def is_command(label: str) -> bool:
    return label.lower().startswith(PREFIX)


@dataclass
class Plan:
    """What the sync job will do with the labels of one event."""

    apply: list[str] = field(default_factory=list)  # known commands from a writer
    strip: list[str] = field(default_factory=list)  # command labels from a non-writer
    unknown: list[str] = field(default_factory=list)  # cmd: labels with no meaning; left in place


def plan_labels(action: str, event_label: str, issue_labels: list[str], sender_can_write: bool) -> Plan:
    """Decide which command labels to apply, strip or report.

    - opened / reopened: every cmd: label on the issue counts (a session creates an issue and
      its labels in one call). From someone without write access they are stripped, because an
      issue form can pre-apply labels for anyone.
    - labeled: only the label just added counts.
    - anything else: nothing.
    """
    if action in ("opened", "reopened"):
        candidates = [label for label in issue_labels if is_command(label)]
    elif action == "labeled":
        candidates = [event_label] if event_label and is_command(event_label) else []
    else:
        candidates = []

    plan = Plan()
    for label in candidates:
        if not sender_can_write:
            plan.strip.append(label)
        elif parse_command(label):
            plan.apply.append(label)
        else:
            plan.unknown.append(label)
    return plan


def last_wins(labels: list[str]) -> dict[str, str]:
    """Collapse commands to one option per field; a later label wins."""
    result: dict[str, str] = {}
    for label in labels:
        parsed = parse_command(label)
        if parsed:
            result[parsed[0]] = parsed[1]
    return result


class RequestError(ValueError):
    """A set-date request that is refused before any API call."""


def parse_date_request(field_name: str, value: str) -> tuple[str, str | None]:
    """(field, YYYY-MM-DD) to set a date, or (field, None) to clear it.

    The refusal never repeats the value: it was typed by a person and the log is public.
    """
    by_lower = {name.lower(): name for name in DATE_FIELDS}
    canonical = by_lower.get(field_name.strip().lower())
    if not canonical:
        raise RequestError(f"field must be one of {', '.join(DATE_FIELDS)}")
    value = value.strip()
    if value.lower() == CLEAR:
        return canonical, None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise RequestError(f"date must be YYYY-MM-DD or {CLEAR!r}")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise RequestError("date is not a real day") from None
    return canonical, value


# --------------------------------------------------------------------------- GitHub I/O


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise GitHubError("no token: set PROJECT_TOKEN, GH_TOKEN or GITHUB_TOKEN")
        self.token = token

    def _request(self, method: str, url: str, body: dict | None = None) -> dict | None:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 404 and method == "DELETE":
                return None  # label already gone
            raise GitHubError(f"{method} {url.split('?')[0]} -> HTTP {err.code}") from None
        return json.loads(raw) if raw else None

    def graphql(self, query: str, variables: dict) -> dict:
        out = self._request("POST", f"{API}/graphql", {"query": query, "variables": variables})
        if not out or out.get("errors"):
            messages = "; ".join(e.get("message", "?") for e in (out or {}).get("errors", []))
            raise GitHubError(f"GraphQL error: {messages or 'empty response'}")
        return out["data"]

    def rest(self, method: str, path: str, body: dict | None = None) -> dict | None:
        return self._request(method, f"{API}{path}", body)


PROJECT_QUERY = """
query($org: String!, $number: Int!) {
  organization(login: $org) {
    projectV2(number: $number) {
      id
      fields(first: 50) {
        nodes {
          ... on ProjectV2SingleSelectField { id name options { id name } }
          ... on ProjectV2Field { id name }
        }
      }
    }
  }
}
"""

ADD_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""

SET_OPTION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field,
    value: {singleSelectOptionId: $option}
  }) { projectV2Item { id } }
}
"""

SET_DATE = """
mutation($project: ID!, $item: ID!, $field: ID!, $date: Date!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field, value: {date: $date}
  }) { projectV2Item { id } }
}
"""

CLEAR_FIELD = """
mutation($project: ID!, $item: ID!, $field: ID!) {
  clearProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field}) {
    projectV2Item { id }
  }
}
"""

ITEMS_QUERY = """
query($org: String!, $number: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isArchived
          fieldValues(first: 30) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2FieldCommon { name } }
              }
              ... on ProjectV2ItemFieldDateValue {
                date
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
          content {
            __typename
            ... on Issue { number title url state repository { name } assignees(first: 5) { nodes { login } } }
            ... on PullRequest { number title url state repository { name } assignees(first: 5) { nodes { login } } }
            ... on DraftIssue { title }
          }
        }
      }
    }
  }
}
"""


@dataclass
class Project:
    id: str
    fields: dict[str, dict]  # name -> {"id": ..., "options": {name: id}}

    def field_id(self, field_name: str) -> str:
        f = self.fields.get(field_name)
        if not f:
            raise GitHubError(f"Project has no field named {field_name!r}")
        return f["id"]

    def option(self, field_name: str, option_name: str) -> tuple[str, str]:
        f = self.fields.get(field_name)
        if not f:
            raise GitHubError(f"Project has no field named {field_name!r}")
        opt = f["options"].get(option_name)
        if not opt:
            raise GitHubError(f"Field {field_name!r} has no option {option_name!r}")
        return f["id"], opt


def parse_project(data: dict) -> Project:
    node = (data.get("organization") or {}).get("projectV2")
    if not node:
        raise GitHubError("Project not found, or the token cannot read it")
    fields = {}
    for f in node["fields"]["nodes"]:
        if not f or "name" not in f:
            continue
        fields[f["name"]] = {
            "id": f["id"],
            "options": {o["name"]: o["id"] for o in f.get("options", [])},
        }
    return Project(id=node["id"], fields=fields)


# --------------------------------------------------------------------------- sync


def can_write(gh: GitHub, repo: str, user: str) -> bool:
    if not user:
        return False
    out = gh.rest("GET", f"/repos/{repo}/collaborators/{urllib.parse.quote(user)}/permission") or {}
    return out.get("role_name") in WRITE_ROLES or out.get("permission") in WRITE_ROLES


def add_item(gh: GitHub, project: Project, content_id: str) -> str:
    """Add an issue or pull request to the Project (a no-op when it is there); its item id."""
    item = gh.graphql(ADD_ITEM, {"project": project.id, "content": content_id})
    return item["addProjectV2ItemById"]["item"]["id"]


def set_option(gh: GitHub, project: Project, item_id: str, field_name: str, option_name: str) -> None:
    field_id, option_id = project.option(field_name, option_name)
    gh.graphql(SET_OPTION, {"project": project.id, "item": item_id, "field": field_id, "option": option_id})


def summary(lines: list[str]) -> None:
    text = "\n".join(lines)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def cmd_sync(args: argparse.Namespace) -> int:
    env = os.environ
    repo = env["REPO"]
    number = env["ISSUE_NUMBER"]
    action = env.get("EVENT_ACTION", "")
    event_label = env.get("EVENT_LABEL", "")
    issue_labels = json.loads(env.get("ISSUE_LABELS") or "[]") or []

    project_gh = GitHub(env.get("PROJECT_TOKEN", ""))
    repo_gh = GitHub(env.get("GITHUB_TOKEN", ""))

    writer = can_write(repo_gh, repo, env.get("SENDER", ""))
    plan = plan_labels(action, event_label, issue_labels, writer)

    project = parse_project(
        project_gh.graphql(PROJECT_QUERY, {"org": env["ORG"], "number": int(env["PROJECT_NUMBER"])})
    )
    item_id = add_item(project_gh, project, env["ISSUE_NODE_ID"])

    lines = [f"### Project sync: {repo}#{number} ({action})", "- in Project: yes"]
    for field_name, option_name in last_wins(plan.apply).items():
        set_option(project_gh, project, item_id, field_name, option_name)
        lines.append(f"- set {field_name} = {option_name}")

    for label in plan.apply + plan.strip:
        repo_gh.rest("DELETE", f"/repos/{repo}/issues/{number}/labels/{urllib.parse.quote(label, safe='')}")
    if plan.strip:
        lines.append(f"- ignored and removed {len(plan.strip)} command label(s): sender has no write access")
    if plan.unknown:
        lines.append(f"- unknown command label(s), left in place: {', '.join(plan.unknown)}")
    summary(lines)
    return 0


def cmd_set_date(args: argparse.Namespace) -> int:
    env = os.environ
    repo = env["REPO"]
    number = env.get("ISSUE_NUMBER", "").strip()
    if not number.isdigit():
        raise RequestError("issue must be a number")
    field_name, date = parse_date_request(env.get("FIELD", ""), env.get("DATE", ""))

    project_gh = GitHub(env.get("PROJECT_TOKEN", ""))
    repo_gh = GitHub(env.get("GITHUB_TOKEN", ""))

    # Read with the caller's own token from the caller's own repository: only its issues resolve.
    issue = repo_gh.rest("GET", f"/repos/{repo}/issues/{number}") or {}
    project = parse_project(
        project_gh.graphql(PROJECT_QUERY, {"org": env["ORG"], "number": int(env["PROJECT_NUMBER"])})
    )
    field_id = project.field_id(field_name)
    item_id = add_item(project_gh, project, issue["node_id"])
    variables = {"project": project.id, "item": item_id, "field": field_id}
    lines = [f"### Project set: {repo}#{number}", "- in Project: yes"]
    if date is None:
        project_gh.graphql(CLEAR_FIELD, variables)
        lines.append(f"- cleared {field_name}")
    else:
        project_gh.graphql(SET_DATE, {**variables, "date": date})
        lines.append(f"- set {field_name} = {date}")
    summary(lines)
    return 0


# --------------------------------------------------------------------------- snapshot


@dataclass
class Item:
    kind: str  # Issue, PullRequest, DraftIssue
    repo: str
    number: int | None
    title: str
    url: str
    state: str  # OPEN, CLOSED, MERGED, or DRAFT for draft items
    values: dict[str, str]
    assignees: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.values.get(FIELD_STATUS, "")

    @property
    def is_open(self) -> bool:
        return self.state in ("OPEN", "DRAFT") and self.status != "Done"

    @property
    def waits_on_maintainer(self) -> bool:
        """An open item assigned to a maintainer is waiting for their decision."""
        return self.is_open and any(login in MAINTAINERS for login in self.assignees)

    def ref(self) -> str:
        if self.kind == "DraftIssue":
            return "draft"
        return f"{self.repo}#{self.number}"


def parse_items(pages: list[dict]) -> list[Item]:
    items: list[Item] = []
    for page in pages:
        for node in page["organization"]["projectV2"]["items"]["nodes"]:
            if not node or node.get("isArchived"):
                continue
            content = node.get("content") or {}
            kind = content.get("__typename", "")
            if kind not in ("Issue", "PullRequest", "DraftIssue"):
                continue
            values = {}
            for v in node["fieldValues"]["nodes"]:
                if not v or not v.get("field"):
                    continue
                name = v["field"].get("name")
                if "name" in v and v["name"] is not None:
                    values[name] = v["name"]
                elif v.get("date"):
                    values[name] = v["date"]
            items.append(
                Item(
                    kind=kind,
                    repo=(content.get("repository") or {}).get("name", ""),
                    number=content.get("number"),
                    title=content.get("title", ""),
                    url=content.get("url", ""),
                    state=content.get("state") or "DRAFT",
                    values=values,
                    assignees=[a["login"] for a in (content.get("assignees") or {}).get("nodes", []) if a],
                )
            )
    return items


def fetch_pages(gh: GitHub, org: str, number: int) -> list[dict]:
    pages, cursor = [], None
    while True:
        data = gh.graphql(ITEMS_QUERY, {"org": org, "number": number, "cursor": cursor})
        node = (data.get("organization") or {}).get("projectV2")
        if not node:
            raise GitHubError("Project not found, or the token cannot read it")
        pages.append(data)
        info = node["items"]["pageInfo"]
        if not info["hasNextPage"]:
            return pages
        cursor = info["endCursor"]


def _clean(text: str) -> str:
    return " ".join(text.split()).replace("|", "/")


def _sort_key(item: Item):
    prio = item.values.get(FIELD_PRIORITY, "P9")
    return (prio, item.repo, item.number or 0, item.title)


def _line(item: Item) -> str:
    ref = f"[{item.ref()}]({item.url})" if item.url else item.ref()
    tags = []
    if item.values.get(FIELD_PRIORITY):
        tags.append(item.values[FIELD_PRIORITY])
    if item.values.get(FIELD_TARGET):
        tags.append(f"target {item.values[FIELD_TARGET]}")
    if item.state in ("CLOSED", "MERGED"):
        tags.append(item.state.lower())
    suffix = f" · {' · '.join(tags)}" if tags else ""
    return f"- {ref} {_clean(item.title)}{suffix}"


def _section(title: str, items: list[Item], limit: int | None = None) -> list[str]:
    items = sorted(items, key=_sort_key)
    out = [f"## {title} ({len(items)})", ""]
    if not items:
        out.append("- none")
    shown = items if limit is None else items[:limit]
    out += [_line(i) for i in shown]
    if limit is not None and len(items) > limit:
        out.append(f"- … {len(items) - limit} more in the Project")
    out.append("")
    return out


def render_status(items: list[Item], project_title: str, generated: dt.datetime, project_url: str = "") -> str:
    open_items = [i for i in items if i.is_open]
    stamp = generated.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    kst = generated.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    where = f"[{project_title}]({project_url})" if project_url else project_title
    lines = [
        f"# Status: {project_title}",
        "",
        f"Generated {stamp} ({kst}) from the organization Project {where} by `project-snapshot`.",
        "Do not edit: change the Project, or add a `cmd:` label to an issue.",
        "",
    ]
    lines += _section("Now", [i for i in open_items if i.status == "Now"])
    lines += _section("In review", [i for i in open_items if i.status == "In review"])
    lines += _section("Waiting on Kade", [i for i in items if i.waits_on_maintainer])
    # Verification is independent of Status: closing an issue moves it to Done, but it stays
    # here until Kade has run the check and sets Verification to Verified.
    for environment in VERIFICATION_ENVIRONMENTS:
        lines += _section(environment, [i for i in items if i.values.get(FIELD_VERIFICATION) == environment])
    lines += _section(
        "NOT VERIFIED", [i for i in items if i.values.get(FIELD_VERIFICATION) == "NOT VERIFIED"], limit=30
    )
    lines += _section("Next", [i for i in open_items if i.status == "Next"])
    lines += _section(
        "Backlog", [i for i in open_items if i.status in ("Backlog", "")], limit=40
    )
    return "\n".join(lines).rstrip() + "\n"


def cmd_snapshot(args: argparse.Namespace) -> int:
    token = os.environ.get("PROJECT_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    gh = GitHub(token)
    pages = fetch_pages(gh, args.org, args.project)
    title = pages[0]["organization"]["projectV2"]["title"]
    url = f"https://github.com/orgs/{args.org}/projects/{args.project}"
    text = render_status(parse_items(pages), title, dt.datetime.now(dt.timezone.utc), url)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {args.out}: {len(text.splitlines())} lines")
    return 0


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="apply one issue event (reads its inputs from the environment)")
    sub.add_parser("set-date", help="set or clear Start or Target (reads its inputs from the environment)")
    snap = sub.add_parser("snapshot", help="export the Project to a Markdown file")
    snap.add_argument("--org", default=os.environ.get("ORG", "star-resonance"))
    snap.add_argument("--project", type=int, default=int(os.environ.get("PROJECT_NUMBER") or 0))
    snap.add_argument("--out", default="STATUS.md")
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            return cmd_sync(args)
        if args.command == "set-date":
            return cmd_set_date(args)
        if not args.project:
            parser.error("--project (or PROJECT_NUMBER) is required")
        return cmd_snapshot(args)
    except (GitHubError, RequestError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

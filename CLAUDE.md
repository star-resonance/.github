# star-resonance: organization `.github`

Public organization-wide files for star-resonance (`resonance-stream`, `resonance-lab`): the
Project bridge, the shared CI workflows, and the default issue forms and pull request template.
It holds no project data.

## Layout

| Path | What |
|---|---|
| `scripts/project_bridge.py` | Bridge: `sync` (issue event to Project fields), `set-date` (an issue's Start or Target) and `snapshot` (Project to `STATUS.md`) |
| `scripts/auto_merge_gate.py` | Auto-merge verdict (`merge`, `wait`, `stop`) from a commit's checks, required checks, draft state and changed files; the issues a merged PR closes |
| `scripts/branch_cleanup.py` | Which `claude/*` and `gate/*` branches to delete: a merged PR's branch still at its head, and a gate branch whose task is gone |
| `scripts/claude_md_check.py` | `CLAUDE.md` at most 100 lines; `.claude/rules/*.md` at most 80 lines with `paths:` |
| `scripts/test_*.py` | Unit tests for the scripts |
| `.github/workflows/project-sync.yml` | Reusable: called by `resonance-stream` and `resonance-lab` on issue events |
| `.github/workflows/project-set.yml`, `project-snapshot.yml` | Reusable: a session's date request (workflow_dispatch in the caller); the export to the caller's `status` branch |
| `.github/workflows/auto-merge.yml` | Reusable: merges a green `claude/*` PR into `main`; inputs `require` (checks) and `protected` (paths); output `merged`; drafts and PRs touching `.github/workflows/` are left to Kade |
| `.github/workflows/branch-cleanup.yml` | Reusable, and runs here: deletes merged `claude/*` branches and leftover `gate/*` branches |
| `.github/workflows/claude-md-check.yml`, `eol-check.yml` | Reusable, and run here: the `CLAUDE.md` limits; LF line endings |
| `.github/workflows/test.yml` | Unit tests on push and PR |
| `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/dependabot.yml` | Defaults for repos without their own; monthly Actions bumps here |

## Rules

- **The bridge never prints issue titles or bodies** (Actions logs are public); keep it that
  way in every new log line, error message and test fixture.
- **Callers use `@main`.** A change merged here applies to every calling repository on its
  next run. Keep inputs, outputs and behaviour backward compatible, or change the callers in
  the same task.
- Command labels are honoured only from people with write access; never relax that.
- The reusable workflows take an optional `runs-on` input; public callers never
  pass it, and a self-hosted runner never runs a public repository's jobs.
- The PR template's sections (*What changed and why*, *Verified*, *NOT VERIFIED*,
  *Wrong turns*) are what `repo-workflow` relies on; change them together.

## Gates

| Part | Gate |
|---|---|
| `scripts/` | `python3 -m unittest discover -s scripts -v` (CI: `test.yml`) |
| Bridge against the real Project | Kade: a test issue with a `cmd:` label and a **Project set** run, then a **Project snapshot** run |
| Workflows | YAML parses; NOT VERIFIED until they have run on GitHub |
| `CLAUDE.md` | `python3 scripts/claude_md_check.py .`; CI `claude-md-check` |
| Line endings | CI `eol-check` |

## State and version control

- No `MEMORY.md`, no handoff files. Work on this repository is tracked in `resonance-stream`
  issues.
- Local and cloud sessions work on `claude/<task>`; a task may hold several commits and gets
  one PR. Kade merges it. A local session opens the PR with Kade's `gh` login; without it, it
  prints the commands for Kade.
- A cloud session cannot attach this repository (its name starts with a dot): files for it come
  as a bundle with the commands for Kade to run.
- Push this repository before the callers when they depend on a change here.

## Overrides of global rules

- **No auto-merge.** A cloud session opens a PR and Kade merges it; GitHub auto-merge stays
  off. A merge here reaches every calling repository on its next run.

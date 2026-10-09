# star-resonance: organization `.github`

Public organization-wide files for star-resonance (`resonance-stream`, `resonance-lab`): the
Project bridge and the default pull request template. It holds no project data. The bridge is
this organization's own copy of Stella Rain's, so the two organizations do not depend on each
other.

## Layout

| Path | What |
|---|---|
| `scripts/project_bridge.py` | Bridge: `sync` (issue event to Project fields) and `snapshot` (Project to `STATUS.md`) |
| `scripts/test_project_bridge.py` | Unit tests for the bridge |
| `.github/workflows/project-sync.yml` | Reusable: called by `resonance-stream` and `resonance-lab` on issue events |
| `.github/workflows/test.yml`, `eol-check.yml` | Unit tests; LF line endings (both run here on push and PR) |
| `.github/PULL_REQUEST_TEMPLATE.md` | Default for repos without their own |

`project-snapshot.yml` in each caller checks out this repository to run `snapshot`; it is not
reusable from here.

## Rules

- **The bridge never prints issue titles or bodies** (Actions logs are public); keep it that
  way in every new log line, error message and test fixture.
- **Callers use `@main`.** A change merged here applies to every calling repository on its
  next run. Keep inputs, outputs and behaviour backward compatible, or change the callers in
  the same task.
- Command labels are honoured only from people with write access; never relax that.
- **Two copies.** `project_bridge.py`, its tests and `project-sync.yml` were copied from
  `stella-rain/.github` at commit `07d8f68`; the script's and the workflow's headers say so. A fix made in one copy is
  ported to the other by hand, and the header's commit updated.
- The PR template's sections (*What changed and why*, *Verified*, *NOT VERIFIED*,
  *Wrong turns*) are what `repo-workflow` relies on; change them together.

## Gates

| Part | Gate |
|---|---|
| `scripts/` | `python3 -m unittest discover -s scripts -v` (CI: `test.yml`) |
| Bridge against the real Project | Kade: a test issue with a `cmd:` label, then a **Project snapshot** run |
| Workflows | YAML parses; NOT VERIFIED until they have run on GitHub |
| `CLAUDE.md` | At most 100 lines (`stella-rain/.github` `scripts/claude_md_check.py`, run with this repository as the argument) |
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

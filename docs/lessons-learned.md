# Lessons Learned

Process and infrastructure lessons earned while building Jeli. Kept separate from
`ARCHITECTURE.md` (what the system is) and `THREAT-MODEL.md` (what it defends
against) — this file is about how the work itself goes wrong, and how to avoid
repeating it.

---

## Git: Separate "move a branch pointer" from "discard working-tree changes"

**Incident (2026-07-08):** while syncing a local `main` branch to a remote
squash-merge, a single `git checkout main && git reset --hard origin/main`
was run to fix one problem (a stale local commit on `main`) but silently
discarded a second, unrelated thing (uncommitted working-tree edits that had
been sitting on the branch being left behind). The two were conflated into one
action and only one had actually been approved. The uncommitted edits were
unstaged, so git never created an object for them — once overwritten, they were
unrecoverable (no stash, no dangling blob, nothing `git fsck` could find).

**Rule: stash before any checkout/reset that might touch a dirty working tree,
even if the plan is to discard it a moment later.**
```bash
git status --short          # check first, always
git stash push -m "describe it"   # if not empty — even for a "throwaway" change
```
A stash is a real git object. It survives branch switches and hard resets, and
is recoverable via `git stash list` / `git stash pop` long after the fact. Plain
uncommitted edits are not — they exist only on disk and vanish the instant
something overwrites the file.

**Rule: treat "reset a branch to match remote" and "discard uncommitted changes"
as two separate, separately-confirmed steps, never one combined operation** —
even when they happen to occur back-to-back on the same branch. Approval for one
is not approval for the other.

---

## Git: Push feature-branch commits promptly, don't let them sit local-only

**Incident (2026-07-08):** two commits were made on a feature branch but never
pushed. The branch's PR was squash-merged (via GitHub, from whatever was already
on `origin`) before those commits were pushed — so they were silently excluded
from the merge and left orphaned on the local branch, requiring a cherry-pick
onto a new branch and a follow-on PR to land them.

**Rule: push after each commit on a feature branch, not just when the PR is
"ready."** Nothing bad happens from pushing early or often on a feature branch.
The alternative — local-only commits that could be merged around by anyone with
push access to the PR — is a silent failure mode with no warning until someone
notices commits missing after the fact.

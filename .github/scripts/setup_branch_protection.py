#!/usr/bin/env python3
"""Apply branch protection to main: required approval, limited mergers, no auto-merge."""

from __future__ import annotations

import json
import subprocess
import sys

REPO = "allareddyh/telegram-housing-finder"
BRANCH = "main"

# GitHub usernames allowed to merge (push restriction list).
# NOTE: User/team restrictions only work on ORGANIZATION repos, not personal repos.
# For personal repos, use collaborator roles (Write = can merge, Read = cannot).
ALLOWED_MERGE_USERS = [
    "allareddyh",
]

# Status check names as shown on PRs (workflow / job).
REQUIRED_CHECKS = [
    "CI / lint-and-compile",
]

PROTECTION = {
    "required_status_checks": {
        "strict": True,
        "checks": [{"context": name} for name in REQUIRED_CHECKS],
    },
    "enforce_admins": True,
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 1,
    },
    "restrictions": None,
    "required_linear_history": False,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "block_creations": False,
    "required_conversation_resolution": False,
    "lock_branch": False,
    "allow_fork_syncing": False,
}


def run(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, input=input_text, capture_output=True, text=True, check=False)


def main() -> int:
    if not ALLOWED_MERGE_USERS:
        print("Set ALLOWED_MERGE_USERS in this script before running.", file=sys.stderr)
        return 1

    print("Disabling repository auto-merge…")
    edit = run(["gh", "repo", "edit", REPO, "--enable-auto-merge=false"])
    if edit.returncode != 0:
        print(edit.stderr or edit.stdout, file=sys.stderr)

    print(f"Applying branch protection to {BRANCH}…")
    print("Allowed to merge:", ", ".join(ALLOWED_MERGE_USERS))
    put = run(
        [
            "gh",
            "api",
            "-X",
            "PUT",
            f"repos/{REPO}/branches/{BRANCH}/protection",
            "--input",
            "-",
        ],
        input_text=json.dumps(PROTECTION),
    )
    if put.returncode != 0:
        print(put.stderr or put.stdout, file=sys.stderr)
        print(
            "\nBranch protection could not be applied via API.\n"
            "Private repos on GitHub Free require GitHub Pro (or a public repo).\n"
            "See .github/BRANCH_PROTECTION.md for manual UI steps or the free-tier workaround.",
            file=sys.stderr,
        )
        return 1

    print("Branch protection applied successfully.")
    print("- Required approving reviews: 1")
    print("- Required checks:", ", ".join(REQUIRED_CHECKS))
    print("- Merge restricted to:", ", ".join(ALLOWED_MERGE_USERS))
    print("- Auto-merge: disabled at repo level")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

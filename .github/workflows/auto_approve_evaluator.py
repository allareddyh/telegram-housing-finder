#!/usr/bin/env python3
"""GitHub Actions auto-approve evaluator for telegram-housing-finder."""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

# ---------------------------------------------------------------------------
# RULES — keep in sync with .github/AUTO_APPROVE.md
# ---------------------------------------------------------------------------
RULES: dict[str, Any] = {
    "max_files": 5,
    "max_changed_lines": 100,
    # dbt-style description-only YAML (not used in this repo; kept for parity)
    "allow_yaml_globs": [],
    "allow_python_globs": ["*.py"],
    "allow_sql_globs": [],
    "reject_docs_only": True,
    "reject_tests_only": True,
    "doc_globs": ["**/*.md"],
    "test_globs": [
        "**/test/**",
        "**/tests/**",
        "**/test_*.py",
        "**/*_test.py",
    ],
    "sensitive_globs": [
        ".github/workflows/**",
        ".github/scripts/**",
        "Dockerfile",
        "Dockerfile.*",
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "**/terraform/**",
        "**/k8s/**",
        "**/helm/**",
        "**/*auth*",
        "**/*rbac*",
        "**/*grant*",
        "**/migrations/**",
        "requirements*.txt",
        "poetry.lock",
        "Pipfile.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "**/macros/**",
        "**/*.ipynb",
        "**/*.md",
        ".env",
        ".env.*",
        ".env.example",
    ],
    # Check-run names as shown on PR checks (workflow name / job name).
    "required_checks_always": [
        "CI / lint-and-compile",
    ],
    "required_checks_python": [
        "Python Review / python-review",
    ],
    # Optional Prism-style bot comment marker when SQL/PY changes need extra review.
    "prism_comment_marker": "",
    "comment_marker": "<!-- auto-approve-evaluator:v1 -->",
    "own_check_names": [
        "evaluate",
        "Auto-Approve Evaluator / evaluate",
    ],
}

API_ROOT = "https://api.github.com"
USER_AGENT = "telegram-housing-finder-auto-approve-evaluator"


@dataclass
class EvaluationResult:
    decision: str  # quiet | deferred | approved
    reason: str
    details: list[str]


class GitHubClient:
    def __init__(self, token: str, repository: str) -> None:
        self.token = token
        self.repository = repository
        self.owner, self.repo = repository.split("/", 1)

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{API_ROOT}{path}"
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} failed ({exc.code}): {payload}") from exc

    def get_authenticated_user(self) -> dict[str, Any]:
        return self._request("GET", "/user")

    def get_pull(self, pull_number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.repository}/pulls/{pull_number}")

    def get_pull_files(self, pull_number: int) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                f"/repos/{self.repository}/pulls/{pull_number}/files?per_page=100&page={page}",
            )
            if not batch:
                break
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return files

    def get_file_content(self, path: str, ref: str) -> Optional[str]:
        encoded = urllib.parse.quote(path, safe="")
        payload = self._request(
            "GET",
            f"/repos/{self.repository}/contents/{encoded}?ref={urllib.parse.quote(ref, safe='')}",
        )
        if payload is None:
            return None
        if isinstance(payload, list):
            return None
        if payload.get("encoding") == "base64":
            import base64

            return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
        return payload.get("content")

    def list_check_runs(self, ref: str) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._request(
                "GET",
                f"/repos/{self.repository}/commits/{ref}/check-runs?per_page=100&page={page}",
            )
            batch = payload.get("check_runs", [])
            runs.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return runs

    def list_commit_statuses(self, ref: str) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/repos/{self.repository}/commits/{ref}/status")
        return payload.get("statuses", [])

    def list_issue_comments(self, issue_number: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/repos/{self.repository}/issues/{issue_number}/comments")

    def upsert_issue_comment(self, issue_number: int, body: str) -> None:
        comments = self.list_issue_comments(issue_number)
        marker = RULES["comment_marker"]
        existing = next((c for c in comments if marker in (c.get("body") or "")), None)
        if existing:
            self._request(
                "PATCH",
                f"/repos/{self.repository}/issues/comments/{existing['id']}",
                {"body": body},
            )
        else:
            self._request(
                "POST",
                f"/repos/{self.repository}/issues/{issue_number}/comments",
                {"body": body},
            )

    def list_reviews(self, pull_number: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/repos/{self.repository}/pulls/{pull_number}/reviews")

    def find_open_pulls_for_branch(self, head_branch: str) -> list[dict[str, Any]]:
        encoded = urllib.parse.quote(f"{self.owner}:{head_branch}", safe="")
        return self._request(
            "GET",
            f"/repos/{self.repository}/pulls?state=open&head={encoded}&per_page=10",
        )

    def approve_pull(self, pull_number: int, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repository}/pulls/{pull_number}/reviews",
            {"event": "APPROVE", "body": body},
        )


def matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def count_changed_lines(files: list[dict[str, Any]]) -> tuple[int, int]:
    total_files = len(files)
    total_lines = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files)
    return total_files, total_lines


def is_docs_only(files: list[dict[str, Any]]) -> bool:
    if not files:
        return False
    return all(matches_any(f["filename"], RULES["doc_globs"]) for f in files)


def is_tests_only(files: list[dict[str, Any]]) -> bool:
    if not files:
        return False
    return all(matches_any(f["filename"], RULES["test_globs"]) for f in files)


def strip_sql_comments_and_ws(source: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    lines = []
    for line in without_block.splitlines():
        lines.append(re.sub(r"--.*$", "", line))
    return " ".join(" ".join(lines).split())


def _drop_leading_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def normalize_python(source: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source.strip()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.body = _drop_leading_docstring(node.body)

    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                node.value.value = ""

    return " ".join(ast.unparse(tree).split())


def parse_yaml_safe(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    try:
        return yaml.safe_load(text)
    except Exception:
        return None


def yaml_without_descriptions(node: Any, path: str = "") -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key).lower() == "description":
                continue
            out[key] = yaml_without_descriptions(value, key_path)
        return out
    if isinstance(node, list):
        return [yaml_without_descriptions(item, f"{path}[]") for item in node]
    return node


def is_description_only_yaml_change(old_text: str, new_text: str) -> bool:
    old_tree = parse_yaml_safe(old_text)
    new_tree = parse_yaml_safe(new_text)
    if old_tree is None or new_tree is None:
        return False
    return yaml_without_descriptions(old_tree) == yaml_without_descriptions(new_tree)


def is_comment_or_format_only_python(old_text: str, new_text: str) -> bool:
    return normalize_python(old_text) == normalize_python(new_text)


def is_comment_or_format_only_sql(old_text: str, new_text: str) -> bool:
    return strip_sql_comments_and_ws(old_text) == strip_sql_comments_and_ws(new_text)


def classify_file(
    file_info: dict[str, Any],
    base_ref: str,
    head_ref: str,
    gh: GitHubClient,
) -> tuple[str, str]:
    """Return (category, detail) where category is allow|reject|skip."""
    path = file_info["filename"]
    status = file_info.get("status", "modified")

    if matches_any(path, RULES["sensitive_globs"]):
        return "reject", f"sensitive path: `{path}`"

    if matches_any(path, RULES["allow_yaml_globs"]):
        if status == "removed":
            return "reject", f"yaml deletion not allowed: `{path}`"
        old_text = gh.get_file_content(path, base_ref) or ""
        new_text = gh.get_file_content(path, head_ref) or ""
        if is_description_only_yaml_change(old_text, new_text):
            return "allow", f"description-only YAML: `{path}`"
        return "reject", f"non-description YAML change: `{path}`"

    if matches_any(path, RULES["allow_python_globs"]):
        if status == "removed":
            return "reject", f"python deletion not allowed: `{path}`"
        old_text = gh.get_file_content(path, base_ref) or ""
        new_text = gh.get_file_content(path, head_ref) or ""
        if is_comment_or_format_only_python(old_text, new_text):
            return "allow", f"comment/format-only Python: `{path}`"
        return "reject", f"logic change in Python: `{path}`"

    if matches_any(path, RULES["allow_sql_globs"]):
        if status == "removed":
            return "reject", f"sql deletion not allowed: `{path}`"
        old_text = gh.get_file_content(path, base_ref) or ""
        new_text = gh.get_file_content(path, head_ref) or ""
        if is_comment_or_format_only_sql(old_text, new_text):
            return "allow", f"comment/format-only SQL: `{path}`"
        return "reject", f"logic change in SQL: `{path}`"

    return "reject", f"file not allow-listed: `{path}`"


def classify_change(
    pull: dict[str, Any],
    files: list[dict[str, Any]],
    gh: GitHubClient,
) -> EvaluationResult:
    details: list[str] = []

    if pull.get("draft"):
        return EvaluationResult("deferred", "Draft PRs are not auto-approved.", details)

    file_count, line_count = count_changed_lines(files)
    details.append(f"Changed files: {file_count}, changed lines: {line_count}")

    if file_count == 0:
        return EvaluationResult("deferred", "No file changes detected.", details)

    if file_count > RULES["max_files"]:
        return EvaluationResult(
            "deferred",
            f"Too many files ({file_count} > {RULES['max_files']}).",
            details,
        )

    if line_count > RULES["max_changed_lines"]:
        return EvaluationResult(
            "deferred",
            f"Diff too large ({line_count} > {RULES['max_changed_lines']} lines).",
            details,
        )

    if RULES["reject_docs_only"] and is_docs_only(files):
        return EvaluationResult(
            "deferred",
            "Docs-only PRs are not auto-approved.",
            details,
        )

    if RULES["reject_tests_only"] and is_tests_only(files):
        return EvaluationResult(
            "deferred",
            "Test-only PRs are not auto-approved.",
            details,
        )

    base_ref = pull["base"]["sha"]
    head_ref = pull["head"]["sha"]

    for file_info in files:
        category, detail = classify_file(file_info, base_ref, head_ref, gh)
        details.append(detail)
        if category != "allow":
            return EvaluationResult(
                "deferred",
                "Change is not allow-listed for auto-approval.",
                details,
            )

    return EvaluationResult("approved", "Change classified as safe for auto-approval.", details)


def _normalize_check_name(name: str) -> str:
    return " ".join(name.split()).lower()


def _check_matches(actual: str, expected: str) -> bool:
    actual_n = _normalize_check_name(actual)
    expected_n = _normalize_check_name(expected)
    return actual_n == expected_n or actual_n.endswith(f"/ {expected_n}") or actual_n.endswith(expected_n)


def evaluate_ci(
    head_sha: str,
    files: list[dict[str, Any]],
    gh: GitHubClient,
) -> EvaluationResult:
    required = list(RULES["required_checks_always"])
    if any(
        matches_any(f["filename"], RULES["allow_python_globs"] + RULES["allow_sql_globs"])
        for f in files
    ):
        required.extend(RULES["required_checks_python"])

    check_runs = gh.list_check_runs(head_sha)
    statuses = gh.list_commit_statuses(head_sha)

    details: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    missing: list[str] = []

    successful_names: set[str] = set()
    for run in check_runs:
        name = run.get("name") or ""
        if name in RULES["own_check_names"] or _check_matches(name, "Auto-Approve Evaluator / evaluate"):
            continue
        conclusion = run.get("conclusion") or run.get("status")
        details.append(f"check-run `{name}`: {conclusion}")
        if conclusion in ("success", "skipped", "neutral"):
            successful_names.add(name)
        elif conclusion in (None, "queued", "in_progress", "pending", "requested", "waiting"):
            pending.append(name)
        else:
            failed.append(name)

    for status in statuses:
        name = status.get("context") or status.get("description") or "status"
        state = status.get("state")
        details.append(f"status `{name}`: {state}")
        if state == "success":
            successful_names.add(name)
        elif state == "pending":
            pending.append(name)
        else:
            failed.append(name)

    if pending:
        return EvaluationResult("quiet", "Required checks still pending.", details)

    if failed:
        return EvaluationResult(
            "deferred",
            "One or more checks failed.",
            details + [f"failed: {', '.join(failed)}"],
        )

    for req in required:
        if any(_check_matches(name, req) for name in successful_names):
            continue
        missing.append(req)

    if missing:
        return EvaluationResult(
            "deferred",
            f"Missing required checks: {', '.join(missing)}",
            details,
        )

    return EvaluationResult("approved", "All required CI checks are green.", details)


def has_prism_complete_comment(comments: list[dict[str, Any]]) -> bool:
    marker = (RULES.get("prism_comment_marker") or "").strip()
    if not marker:
        return True
    return any(marker in (c.get("body") or "") for c in comments)


def bot_already_approved(reviews: list[dict[str, Any]], bot_login: str) -> bool:
    bot_login = bot_login.lower()
    for review in reviews:
        user = review.get("user") or {}
        if (user.get("login") or "").lower() != bot_login:
            continue
        if (review.get("state") or "").upper() == "APPROVED":
            return True
    return False


def build_comment(decision: str, reason: str, details: list[str]) -> str:
    marker = RULES["comment_marker"]
    if decision == "approved":
        headline = "✅ **Auto-approve: you can merge**"
        footer = (
            "All safety rules and required CI checks passed. "
            "A bot approval was added — a human should still merge when ready."
        )
    else:
        headline = "🛑 **Auto-approve: needs human review**"
        footer = "This PR did not meet auto-approve rules. Please review manually."

    detail_block = "\n".join(f"- {line}" for line in details[:20])
    return (
        f"{marker}\n"
        f"{headline}\n\n"
        f"{reason}\n\n"
        f"{detail_block}\n\n"
        f"{footer}"
    )


def load_trigger_context() -> tuple[str, str, int]:
    """Return (head_sha, trigger_name, pull_number) from Actions event payload."""
    event_name = os.environ.get("GITHUB_EVENT_NAME", "").strip()

    if event_name == "check_run":
        check_run = json.loads(os.environ.get("GITHUB_EVENT_CHECK_RUN", "") or "{}")
        if (check_run.get("conclusion") or "").lower() != "success":
            raise QuietExit("Triggering check_run was not successful; exiting.")

        trigger_name = check_run.get("name") or ""
        if trigger_name in RULES["own_check_names"] or trigger_name.startswith("Auto-Approve Evaluator"):
            raise QuietExit("Ignoring self-triggered check_run.")

        pull_requests = check_run.get("pull_requests") or []
        if not pull_requests:
            raise QuietExit("No associated pull request; exiting quietly.")

        pull_number = int(pull_requests[0]["number"])
        head_sha = check_run.get("head_sha") or pull_requests[0].get("head", {}).get("sha", "")
        return head_sha, trigger_name, pull_number

    if event_name == "workflow_run":
        workflow_run = json.loads(os.environ.get("GITHUB_EVENT_WORKFLOW_RUN", "") or "{}")
        if (workflow_run.get("conclusion") or "").lower() != "success":
            raise QuietExit("Triggering workflow_run was not successful; exiting.")
        if workflow_run.get("event") != "pull_request":
            raise QuietExit("workflow_run was not from a pull_request; exiting.")

        trigger_name = workflow_run.get("name") or ""
        if trigger_name in ("Auto-Approve Evaluator",):
            raise QuietExit("Ignoring self-triggered workflow_run.")

        head_sha = workflow_run.get("head_sha") or ""
        head_branch = workflow_run.get("head_branch") or ""
        if not head_sha or not head_branch:
            raise QuietExit("workflow_run missing head metadata; exiting.")

        token = os.environ.get("GITHUB_TOKEN", "").strip()
        repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
        gh = GitHubClient(token, repository)
        pulls = gh.find_open_pulls_for_branch(head_branch)
        if not pulls:
            raise QuietExit("No open pull request for workflow_run branch; exiting.")
        pull_number = int(pulls[0]["number"])
        return head_sha, trigger_name, pull_number

    raise RuntimeError(f"Unsupported GITHUB_EVENT_NAME: {event_name or '(empty)'}")


class QuietExit(Exception):
    """Expected early exit without failure."""


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token:
        print("GITHUB_TOKEN not set; exiting quietly.")
        return 0
    if not repository:
        print("GITHUB_REPOSITORY not set; exiting.")
        return 1

    try:
        head_sha, trigger_name, pull_number = load_trigger_context()
    except QuietExit as exc:
        print(str(exc))
        return 0

    gh = GitHubClient(token, repository)
    bot = gh.get_authenticated_user()
    bot_login = bot.get("login") or ""

    pull = gh.get_pull(pull_number)
    if pull.get("state") != "open":
        print("PR is not open; exiting.")
        return 0

    print(f"Evaluating PR #{pull_number} at {head_sha} (trigger: {trigger_name})")

    files = gh.get_pull_files(pull_number)

    ci_result = evaluate_ci(head_sha, files, gh)
    if ci_result.decision == "quiet":
        print(ci_result.reason)
        return 0

    if ci_result.decision == "deferred":
        gh.upsert_issue_comment(
            pull_number,
            build_comment("deferred", ci_result.reason, ci_result.details),
        )
        print(ci_result.reason)
        return 0

    change_result = classify_change(pull, files, gh)
    if change_result.decision != "approved":
        combined = ci_result.details + change_result.details
        gh.upsert_issue_comment(
            pull_number,
            build_comment("deferred", change_result.reason, combined),
        )
        print(change_result.reason)
        return 0

    py_or_sql = any(
        matches_any(f["filename"], RULES["allow_python_globs"] + RULES["allow_sql_globs"])
        for f in files
    )
    if py_or_sql and RULES.get("prism_comment_marker"):
        comments = gh.list_issue_comments(pull_number)
        if not has_prism_complete_comment(comments):
            gh.upsert_issue_comment(
                pull_number,
                build_comment(
                    "deferred",
                    "Waiting for Prism-style review comment before auto-approve.",
                    ci_result.details + change_result.details,
                ),
            )
            print("Prism review comment missing.")
            return 0

    reviews = gh.list_reviews(pull_number)
    if not bot_already_approved(reviews, bot_login):
        gh.approve_pull(
            pull_number,
            "Auto-approved: safe comment/format-only change with green required CI.",
        )

    combined_details = ci_result.details + change_result.details
    gh.upsert_issue_comment(
        pull_number,
        build_comment("approved", change_result.reason, combined_details),
    )
    print("Auto-approved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QuietExit as exc:
        print(str(exc))
        raise SystemExit(0) from exc
    except Exception as exc:
        print(f"Evaluator failed: {exc}", file=sys.stderr)
        raise

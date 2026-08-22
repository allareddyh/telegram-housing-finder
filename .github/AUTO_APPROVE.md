# Auto-Approve Evaluator

This repository uses a GitHub Actions **Auto-Approve Evaluator** that can automatically **approve** small, safe pull requests after required CI is green. It never auto-merges — a human always clicks merge.

Implementation:

- Workflow: [`.github/workflows/auto_approve_evaluator.yml`](workflows/auto_approve_evaluator.yml)
- Rules engine: [`.github/workflows/auto_approve_evaluator.py`](workflows/auto_approve_evaluator.py)

## Flow

1. A PR is opened or updated → normal CI runs (`CI`, and `Python Review` when `*.py` changes).
2. When any check run completes successfully, the evaluator wakes (doorbell pattern).
3. The evaluator confirms **all required checks** are green for the PR head SHA.
4. It classifies the diff:
   - **Safe + green CI** → bot adds an **Approve** review and a single upserted PR comment: “you can merge”.
   - **Risky / too large / not allow-listed / CI incomplete** → one upserted “needs human review” comment (or silence while checks are still pending).
5. A human merges manually. The bot never calls `gh pr merge`.

## When auto-approve is allowed

All of the following must pass.

### 1. Safe change type

Every changed file must be allow-listed **and** pass content checks:

| Pattern | Rule |
|---------|------|
| `*.py` (root app code) | Comment, docstring, or whitespace/formatting only. Logic changes are rejected. |
| `models/Source/**/*.yml\|yaml` | Not used in this repo (rule kept for parity). Would allow **description-only** YAML edits. |
| `*.sql` | Not used in this repo. Would allow comment/format-only SQL edits. |

**Explicitly rejected PR types (even if small):**

- Docs-only (`*.md`)
- Test-only (`tests/**`, `test_*.py`, `*_test.py`)

### 2. Required CI green (head SHA)

| Check | When required |
|-------|----------------|
| `CI / lint-and-compile` | Always |
| `Python Review / python-review` | When any `*.py` file changes |

Pending checks → evaluator exits quietly (no comment).  
Failed or missing required checks → “needs human review” comment, no approve.

The evaluator ignores its own check run (`Auto-Approve Evaluator / evaluate`) to avoid loops.

### 3. Size limits

- ≤ **5** changed files
- ≤ **100** changed lines (additions + deletions)
- PR must **not** be a draft

### 4. Sensitive paths (always reject)

- `.github/workflows/**`, `.github/scripts/**`
- Docker / compose files
- Terraform, Kubernetes, Helm paths
- Auth / RBAC / grant paths (`*auth*`, `*rbac*`, `*grant*`)
- Migrations, lockfiles, dependency manifests (`requirements*.txt`, etc.)
- Notebooks (`*.ipynb`)
- Docs (`*.md`)
- Secrets templates (`.env`, `.env.*`, `.env.example`)

## Bot behavior

- **Idempotent comment**: one PR comment upserted via hidden HTML marker `<!-- auto-approve-evaluator:v1 -->`.
- **Idempotent approve**: skips review if the bot user already approved.
- **No trusted-author gate**: team membership is not considered.
- **No auto-merge**: merge is always manual.

## Required secret

Add a fine-grained PAT (or classic PAT with equivalent scopes) as a repository secret:

| Secret name | Purpose |
|-------------|---------|
| `AUTO_APPROVE_TOKEN` | Preferred |
| `MKT_GH_TOKEN` | Fallback alias |

**Recommended fine-grained PAT permissions (this repo only):**

- Pull requests: **Read and write**
- Issues: **Read and write** (PR comments)
- Contents: **Read**
- Checks: **Read**
- Commit statuses: **Read** (via Checks API)

The PAT must belong to a bot or machine user whose approval satisfies branch protection (if enabled).

The workflow maps the secret to `GITHUB_TOKEN` for the Python script.

## Updating rules

Edit the `RULES` dict at the top of `auto_approve_evaluator.py` and mirror changes here. Keep required check names aligned with job names in:

- [`.github/workflows/ci.yml`](workflows/ci.yml) → `CI / lint-and-compile`
- [`.github/workflows/python_review.yml`](workflows/python_review.yml) → `Python Review / python-review`

## Smoke testing

### Setup (once)

1. Merge the workflows to `main`.
2. Add `AUTO_APPROVE_TOKEN` under **Settings → Secrets and variables → Actions**.
3. Ensure branch protection (if any) allows the bot user to approve.

### Happy path

1. Branch from `main`, edit `filters.py` — add a comment or reformat whitespace only.
2. Open a PR → wait for `CI / lint-and-compile` and `Python Review / python-review` to pass.
3. Expect:
   - Bot **Approve** review
   - Comment: **Auto-approve: you can merge**
4. Merge manually.

### Negative cases

| Scenario | Expected |
|----------|----------|
| Change logic in `filters.py` | Deferred comment, no approve |
| README-only PR | Deferred (“docs-only”) |
| Edit `requirements.txt` | Deferred (sensitive path) |
| Edit `.github/workflows/ci.yml` | Deferred (sensitive path) |
| > 5 files or > 100 lines | Deferred (too large) |
| Draft PR | Deferred |
| CI failing | Deferred after checks settle |
| Only one check green, other still running | Quiet until all required checks finish |

### Local rule checks (no GitHub)

```powershell
python .github/workflows/auto_approve_evaluator.py
```

The script expects GitHub Actions env vars; for local normalization checks, import helpers from a small REPL or temporary script.

# Branch protection — mandatory approval, limited mergers, no auto-merge

Right now anyone with **Write** access sees **Merge pull request** once checks pass (as in PR #2). To change that you need either **branch protection** (best) or **tighter collaborator roles** (free-tier workaround).

## What you want

| Goal | How |
|------|-----|
| PR required before landing on `main` | Branch protection |
| At least 1 approval before merge | Branch protection → required reviews |
| Only **specific people** can click Merge | Branch protection → **Restrict who can push** to `main` |
| No bot auto-merge | Already off; no workflow calls merge API |

Auto-approve bot can add the **approval** on safe PRs; only people on the merge allow-list can still click **Merge**.

---

## Option A — Branch protection (recommended)

**Requires GitHub Pro** on a private personal repo, **or** a **public** repository.  
(API returns 403 on GitHub Free private — same limit applies in **Settings → Branches**.)

### Steps (GitHub UI)

1. Open **https://github.com/allareddyh/telegram-housing-finder/settings/branches**
2. **Add branch protection rule** (or **Add ruleset** under Settings → Rules).
3. Branch name pattern: **`main`**
4. Turn on:
   - **Require a pull request before merging**
     - Required approvals: **1**
     - Dismiss stale pull request approvals when new commits are pushed
   - **Require status checks to pass before merging**
     - Search and add: **`CI / lint-and-compile`**
     - **Require branches to be up to date before merging**
   - **Restrict who can push to matching branches** ← limits who can merge (**organization repos only**; not available on personal repos — use collaborator roles instead)
   - **Do not allow bypassing the above settings** (optional; applies rules to admins too)
5. Under **Settings → General → Pull Requests**: keep **Allow auto-merge** **disabled**.

### Personal vs organization repo

| Feature | Personal repo (`allareddyh/...`) | Organization repo |
|---------|----------------------------------|-------------------|
| Required PR + approvals | Yes | Yes |
| Required status checks | Yes | Yes |
| Restrict merge to named users | **No** — use collaborator **Write** vs **Read** roles | Yes, via push restrictions |

**Current repo:** public personal repo — branch protection with **1 required approval** and **`CI / lint-and-compile`** is enabled on `main`.

### Apply via script (Pro or public repo only)

Edit allowed mergers in `.github/scripts/setup_branch_protection.py`:

```python
ALLOWED_MERGE_USERS = [
    "allareddyh",
    # "other-github-username",
]
```

Then run:

```powershell
python .github/scripts/setup_branch_protection.py
```

---

## Option B — Free private repo (no Pro)

Branch protection is **not available** on GitHub Free private repos. Use **role-based access** instead:

1. **Settings → Collaborators** (or **Manage access**)
2. Give **Write** (or **Maintain**) only to people who may merge — usually 1–3 accounts.
3. Give everyone else **Read** (or **Triage**):
   - They can comment and review.
   - They **cannot** push to `main` or click **Merge pull request**.

**Limitation:** The repo **owner** (`allareddyh`) can always merge unless branch protection exists. For the owner to be blocked too, you need Option A (Pro/public).

---

## How this works with auto-approve

1. CI runs on the PR.
2. Auto-Approve Evaluator may add an **Approve** review on safe, comment-only changes.
3. Branch protection (Option A) requires that approval + green checks.
4. Only users on the merge allow-list click **Merge** manually.

---

## Quick checklist for your screenshot (PR #2)

PR #2 shows **Ready to merge** with no review requirement because:

- [ ] Branch protection is not enabled on `main`
- [ ] No required approving review
- [ ] No push/merge restriction list

After Option A or B, that green merge button will either disappear (wrong user) or stay disabled until approval (required reviews).

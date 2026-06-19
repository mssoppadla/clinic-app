# Deployment (GitHub CI/CD → VPS) — mirrors the cpmai pattern

> **First create an EMPTY GitHub repo** (no README/.gitignore/license) and push this code to it — see "Create the GitHub repo" at the bottom.

## The flow you asked for
```
local docker test (make docker-test)
   → commit + push to a feature branch
   → open PR to main
   → CI runs automatically:  backend-ci · frontend-ci · security-scan   (advisory, per-area)
   → YOU manually review + approve + merge the PR        ← manual gate #1
   → merge to main triggers  deploy.yml:
        job 1  test          (STRICT: pytest + backward-compat gate)
        job 2  migration-drift (STRICT: schema on real Postgres + alembic check/upgrade)
        job 3  deploy        → waits for the "production" Environment approval
   → YOU click Approve on the production deployment        ← manual gate #2 (the GitHub screen)
        → Actions SSHes to the VPS and runs scripts/vps/deploy.sh
          (data-guard snapshot → sync → build → bootstrap+seed → health gate →
           canary smoke → data-preservation verify → rollback on any failure)
        → post-deploy public smoke (curl health + page)
```
Two manual gates, exactly as cpmai: (1) approve+merge the PR, (2) approve the production deploy.

## One-time GitHub setup
1. **Branch protection** (Settings → Branches → add rule for `main`):
   - Require a pull request before merging.  **Solo founder:** set *Required approvals = 0*
     (GitHub won't let you approve your own PR; with 1 required you'd be unable to merge).
     You still go through the PR and click **Merge** yourself — that's manual gate #1.
     Add a 2nd Code Owner later to turn on real review approvals.
   - Require status checks to pass: select `backend-ci`, `frontend-ci`, `security-scan`.
   - Do **not** enable "Require review from Code Owners" while you're the only owner.
   - (Optional) Require branches to be up to date before merging.
2. **Production environment** (Settings → Environments → New environment → `production`):
   - Add **Required reviewers** = you  → this is the manual approval button for deploys.
   - Restrict to the `main` branch (deployment branches).
   - Add the environment **secrets** below.

## Secrets (Settings → Environments → production → Secrets)
| Secret | What |
|--------|------|
| `VPS_HOST` | VPS hostname or IP |
| `VPS_USER` | deploy user (e.g. `deploy`) |
| `VPS_SSH_KEY` | private key (ed25519 recommended) for that user |
| `VPS_HOST_KEY` | output of `ssh-keyscan <VPS_HOST>` (host-key pinning) |

> App secrets (WhatsApp token, Bhashini key, Postgres password) are NOT GitHub secrets —
> they live in `/opt/clinic-app/.env` on the VPS, or are set at runtime via the admin screen.

## One-time VPS prep (co-hosted with cpmai, isolated)
```bash
sudo mkdir -p /opt/clinic-app && sudo chown deploy:deploy /opt/clinic-app
git clone <repo-url> /opt/clinic-app
cd /opt/clinic-app
cp .env.example .env        # set POSTGRES_PASSWORD etc. (never committed)
docker compose -p clinic-saas -f deploy/docker-compose.yml up -d --build
```
Separate compose project (`-p clinic-saas`) + its own DB + Caddy vhost → cpmai is never affected.

## Your day-to-day loop
```bash
make docker-test     # prod-like local test in Docker
git checkout -b my-change && git commit -am "..." && git push -u origin my-change
# open PR → wait for green CI → approve & merge → approve the production deploy in GitHub
```

## How this maps to cpmai
- `backend-ci.yml` / `frontend-ci.yml` / `security-scan.yml` = same advisory per-area CI.
- `deploy.yml` = same shape: strict **test** gate + **migration-drift** gate + **deploy** job gated by the `production` Environment, SSH with `VPS_HOST_KEY` pinning, runs an on-VPS `scripts/vps/deploy.sh`, then a public post-deploy smoke.
- Difference: cpmai uses pgvector + a 27-step smoke; clinic-app uses plain Postgres + the canary booking smoke (`e2e/smoke.py`) and a data-preservation guard. Job names and gates are aligned.


## Create the GitHub repo (one-time)
You need an **empty** repository on GitHub to push to. Two ways:

**A. Website**
1. github.com → **New repository** → name e.g. `clinic-app` → **Private**.
2. **Do NOT** tick "Add a README", ".gitignore", or "license" — keep it empty so the first push is clean.
3. Create, then copy the repo URL and run locally:
   ```
   git remote add origin https://github.com/mssoppadla/clinic-app.git
   git branch -M main
   git push -u origin main
   ```

**B. GitHub CLI** (creates the empty repo and pushes in one step, from the project folder):
   ```
   gh repo create clinic-app --private --source . --push
   ```

After the push, the workflows appear under the repo's **Actions** tab. Then do the
branch-protection + `production` environment setup above. CODEOWNERS auto-requests you as
reviewer; `pull_request_template.md` pre-fills the PR checklist.

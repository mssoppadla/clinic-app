# Runbook — deploy fails with "ssh: connect to host … port 22: Connection timed out"

Applies to **clinic-app** and **tovaitech-site** (both deploy to the same Hostinger VPS
`187.127.163.86` via GitHub Actions → SSH → `scripts/vps/deploy.sh`).

## Symptom
The GitHub Actions `deploy` job fails in the SSH step:

```
ssh: connect to host *** port 22: Connection timed out
##[error]Process completed with exit code 255   (ssh transport failure)
```

With the retry loop (clinic-app) it looks like:

```
*** attempt 1/5
ssh connection failed (exit 255) on attempt 1
... (all attempts time out)
```

## Diagnosis — it's a GitHub-runner-IP block, not the code or the workflow
Two facts, checked together, pin it down:

1. **From a normal machine, port 22 is OPEN.** From your laptop:
   ```powershell
   Test-NetConnection -ComputerName 187.127.163.86 -Port 22   # TcpTestSucceeded : True
   ```
2. **From GitHub runners it times out at the TCP layer** — the SYN is dropped *before*
   SSH auth (exit 255, "Connection timed out", not "Connection refused", not "Permission denied").

GitHub-hosted runners use a large, **rotating** pool of egress IPs. "Open from home, dropped
for runners" = the VPS is **silently dropping packets from the runner IPs**. A timeout (drop)
rather than a refusal points at a firewall/ban with a **DROP** action.

This is intermittent by nature: some runs land on an IP that isn't (yet) dropped and succeed;
the next run draws a different IP and fails. The retry loop in `.github/workflows/deploy.yml`
buys resilience against *transient* drops but **cannot** beat a persistent block of the IP it
drew — it just retries the same blocked IP.

## Confirm on the VPS (run from your working SSH session)
```bash
sudo fail2ban-client status sshd          # banned IPs — look for GitHub/Azure ranges
sudo iptables -S | grep -iE "drop|f2b|22" # DROP rules on 22 / fail2ban (f2b-*) chains
sudo ufw status verbose                   # is 22 restricted to specific source IPs?
journalctl -u ssh -n 50 --no-pager        # sshd side: any auth churn?
```
Also check **Hostinger hPanel → VPS → Firewall** for an SSH source-IP rule.

## Fix (pick per finding) — one-time, no self-hosted runner, no repeated installs
- **fail2ban is banning the runners (most common).** TCP timeouts never reach `sshd`, so a
  *legit key-auth* deploy shouldn't trip it — but a DROP ban from earlier churn lingers and
  the ban action is DROP. Options:
  - Unban and stop re-banning the deploy path. Since runner IPs rotate, per-IP `ignoreip`
    is futile; instead make the ban action **REJECT** (fails fast instead of 2-min timeouts)
    and ensure the deploy only ever does **key auth** (no password attempts):
    `sudo fail2ban-client set sshd unbanip <ip>` for any stuck ones; review `bantime`/`maxretry`.
  - If fail2ban offers no value here (key-only auth already enforced), consider disabling the
    `sshd` jail and relying on key-only SSH + a firewall.
- **A source-IP allowlist on port 22 (ufw / Hostinger firewall).** Home works only if your IP
  is allowed; runners never are. With **key-only** auth it's safe to open 22 to `0.0.0.0/0`
  (`sudo ufw allow 22/tcp`) — keys, not the firewall, are the access control. Maintaining
  GitHub's published IP ranges (`https://api.github.com/meta` → `actions`) is possible but
  high-maintenance because they rotate.
- **Hardening, not a fix:** the workflow retry (clinic-app, and add to tovaitech-site) absorbs
  transient drops. Keep it, but it is not a substitute for the VPS-side change above.

## Related gap — tovaitech-site has no SSH secrets
`tovaitech-site`'s deploy fails earlier, at the **Configure SSH** step (exit 1, not a timeout):
with `VPS_HOST_KEY`/`VPS_HOST` unset it never creates `~/.ssh/known_hosts`, then
`chmod 600 ~/.ssh/known_hosts` errors on a missing file. Secrets are **per-repo** — they do
not carry over from clinic-app. Set them once (values: host/user are known; key + host-key
are yours):
```bash
gh secret set VPS_HOST     -R mssoppadla/tovaitech-site --env production --body "187.127.163.86"
gh secret set VPS_USER     -R mssoppadla/tovaitech-site --env production --body "deploy"
gh secret set VPS_HOST_KEY -R mssoppadla/tovaitech-site --env production --body "$(ssh-keyscan 187.127.163.86 2>/dev/null)"
gh secret set VPS_SSH_KEY  -R mssoppadla/tovaitech-site --env production < /path/to/deploy_private_key
```
Also confirm `/opt/tovaitech-site` exists on the VPS (its `deploy.sh` `cd`s there).

## Quick reference
| Failure | Layer | Meaning | Action |
|---|---|---|---|
| `Connection timed out`, exit 255 | TCP (pre-auth) | runner IP dropped by VPS firewall/ban | fix VPS firewall/fail2ban |
| `Connection refused`, exit 255 | TCP | nothing listening / REJECT | check sshd up; ban action = REJECT |
| `Permission denied (publickey)` | SSH auth | wrong/missing `VPS_SSH_KEY` | fix the key secret |
| `Host key verification failed` | SSH auth | stale/missing `VPS_HOST_KEY` | refresh `ssh-keyscan` secret |
| `chmod … known_hosts: No such file`, exit 1 | pre-SSH | secrets unset → known_hosts never written | set the 4 VPS secrets |

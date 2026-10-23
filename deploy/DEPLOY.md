# Deploy NullOne to AWS EC2

Minimal deployment using shell scripts in `deploy/`. No Terraform required.

## What you get

- One **EC2 instance** in **us-east-1** running NullOne
- **chrony** for accurate release timing
- **systemd timer** that starts `nullone run` every **Thursday at 10:00 AM ET** (runner waits until 10:30 AM ET)
- **`deploy/manage.sh`** for status, logs, dry-run, and manual runs

## Prerequisites

- AWS account
- SSH key pair (EC2 `.pem` file — separate from `kalshi-private-key.pem`)
- Kalshi API key + RSA private key (`.pem`)
- This repo cloned locally with a working `.env`

---

## Connecting to the server

### Find the public IP

AWS Console → **EC2 → Instances** → select your instance → copy **Public IPv4 address**.

Use the public IPv4 address, not the private `172.31.x.x` address. Allocate an Elastic IP if you need a fixed address after stop/start.

### Log in (from your laptop)

Run on your **Mac**, not inside an existing SSH session:

```bash
chmod 400 ~/path/to/your-ec2-key.pem
ssh -i ~/path/to/your-ec2-key.pem ec2-user@<EC2_PUBLIC_IP>
```

- Replace `<EC2_PUBLIC_IP>` with the real IP — **do not include angle brackets**
- **Amazon Linux:** login user is `ec2-user`
- **Ubuntu 24.04:** login user is `ubuntu`
- The `-i` file is your **EC2 key pair**, not the Kalshi private key

First connect may prompt to trust the host fingerprint — type `yes`.

### Log out

```bash
exit
```

Or press `Ctrl+D`. The instance keeps running; the weekly timer is unaffected.

### Copy files to the server (from your laptop)

```bash
scp -i ~/path/to/your-ec2-key.pem \
  /path/to/NullOne/.env \
  /path/to/NullOne/kalshi-private-key.pem \
  ec2-user@<EC2_PUBLIC_IP>:/tmp/
```

Then on the server:

```bash
cd /opt/nullone/NullOne
sudo deploy/setup-env.sh /tmp/.env
rm -f /tmp/.env /tmp/kalshi-private-key.pem
```

### Running NullOne commands

`/opt/nullone/.env` is mode `600` and owned by the `nullone` user. Run `manage.sh` and `nullone` **as that user**:

```bash
cd /opt/nullone/NullOne
sudo -u nullone ./deploy/manage.sh status
sudo -u nullone ./.venv/bin/nullone ready --skip-tests
```

Plain `./deploy/manage.sh` as `ec2-user` will fail with `Permission denied` on `.env`.

**Optional alias** (add to `~/.bashrc`):

```bash
alias nullone-manage='sudo -u nullone /opt/nullone/NullOne/deploy/manage.sh'
```

### Common operations

| Task | Command (on EC2) |
|------|-------------------|
| Timer + clock + next release | `sudo -u nullone ./deploy/manage.sh status` |
| Readiness check | `sudo -u nullone ./.venv/bin/nullone ready --skip-tests` |
| Discover Kalshi tickers | `sudo -u nullone ./deploy/manage.sh discover --week YYYY-MM-DD` |
| Book connectivity | `sudo -u nullone ./deploy/manage.sh dry-run --week YYYY-MM-DD` |
| Paper simulate (fixture) | `sudo -u nullone ./deploy/manage.sh simulate` |
| Manual release run | `sudo -u nullone ./deploy/manage.sh run-now --mode paper` |
| Latest JSON log | `sudo -u nullone ./deploy/manage.sh logs` |
| Live journal (release day) | `sudo -u nullone ./deploy/manage.sh tail-logs` |
| Recent timer failures | `sudo journalctl -u nullone-release.service -n 50 --no-pager` |
| Enable weekly timer | `sudo NULLONE_MODE=paper deploy/install-timer.sh` |
| Update code | `sudo -u nullone ./deploy/manage.sh update` |

---

## Step 1: Launch EC2

1. Open **AWS Console → EC2 → Launch instance**.
2. **Name:** `nullone-release`
3. **AMI:** Ubuntu Server 24.04 LTS (recommended; deploy scripts use `apt`) — Amazon Linux 2023 works with manual Python 3.12 setup
4. **Instance type:** `t3.small` (enough for weekly runs)
5. **Key pair:** create or select one
6. **Network:** default VPC is fine
7. **Security group:** allow **SSH (22/tcp)** from your IP for admin access; allow outbound:
   - HTTPS (443/tcp) — EIA + Kalshi APIs
   - NTP (123/udp) — clock sync
8. **Storage:** 20 GB gp3
9. **Region:** **us-east-1** (N. Virginia)
10. Launch.

Note the **public IP** (or allocate an Elastic IP if you want a fixed address).

---

## Step 2: Connect and clone the repo

```bash
ssh -i ~/path/to/key.pem ubuntu@<EC2_PUBLIC_IP>

sudo mkdir -p /opt/nullone
sudo git clone https://github.com/theorem-stack/NullOne.git /opt/nullone/NullOne
cd /opt/nullone/NullOne
```

Or copy the repo with `scp -r` from your laptop if it is not on GitHub yet:

```bash
scp -i ~/path/to/key.pem -r NullOne ec2-user@<EC2_PUBLIC_IP>:/tmp/
ssh -i ~/path/to/key.pem ec2-user@<EC2_PUBLIC_IP>
sudo mv /tmp/NullOne /opt/nullone/NullOne
cd /opt/nullone/NullOne
```

Private repos need a GitHub PAT (Contents: Read) or SSH deploy key — password auth is not supported.

---

## Step 3: Bootstrap the server

**Ubuntu:**

```bash
cd /opt/nullone/NullOne
sudo deploy/bootstrap.sh
```

This installs Python 3.12, chrony, creates the `nullone` system user, a venv, and `/opt/nullone/.env` from `.env.example`.

**Amazon Linux 2023:** `bootstrap.sh` uses `apt-get` and will not run as-is. Install `python3.12`, `python3.12-pip`, and `chrony` via `dnf`, create the venv with `python3.12 -m venv .venv`, and `pip install -e .` as the `nullone` user. See README deployment notes.

---

## Step 4: Copy secrets from your laptop

**On your laptop** (from the repo root):

```bash
scp -i ~/path/to/key.pem .env kalshi-private-key.pem ec2-user@<EC2_PUBLIC_IP>:/tmp/
```

**On the server:**

```bash
cd /opt/nullone/NullOne
sudo deploy/setup-env.sh /tmp/.env
rm -f /tmp/.env /tmp/kalshi-private-key.pem
```

`setup-env.sh` installs `/opt/nullone/.env`, rewrites paths for the server, and installs the Kalshi PEM at `/opt/nullone/kalshi-private-key.pem` (mode 600).

Edit if needed:

```bash
sudo nano /opt/nullone/.env
```

Important variables:

```env
KALSHI_API_KEY=...
KALSHI_PRIVATE_KEY_PATH=/opt/nullone/kalshi-private-key.pem
KALSHI_GAS_STORAGE_SERIES=KXGAS
NULLONE_MODE=paper
NULLONE_RELEASE_CAPITAL_USD=10
NULLONE_EXECUTION_STRATEGY=auto
NULLONE_LOG_DIR=/opt/nullone/logs/releases
```

---

## Updating environment variables or Kalshi PEM

Use this process for key rotation, switching from read-only to write-scoped Kalshi keys, changing `NULLONE_MODE`, updating release capital, or changing execution strategy.

### 1. Update local secrets

On your laptop:

```bash
cd /path/to/NullOne
nano .env
```

Update values such as:

```env
KALSHI_API_KEY=...
KALSHI_PRIVATE_KEY_PATH=/path/to/NullOne/kalshi-private-key.pem
NULLONE_MODE=paper
NULLONE_RELEASE_CAPITAL_USD=10
NULLONE_EXECUTION_STRATEGY=auto
```

If Kalshi created a new private key, replace your local `kalshi-private-key.pem`. The `KALSHI_API_KEY` and PEM must be from the same Kalshi key creation event.

Validate locally:

```bash
./.venv/bin/nullone ready --skip-tests
```

### 2. Copy updated secrets to EC2

Run from your laptop:

```bash
scp -i ~/path/to/key.pem \
  /path/to/NullOne/.env \
  /path/to/NullOne/kalshi-private-key.pem \
  ec2-user@<EC2_PUBLIC_IP>:/tmp/
```

### 3. Install on EC2

```bash
ssh -i ~/path/to/key.pem ec2-user@<EC2_PUBLIC_IP>
cd /opt/nullone/NullOne
sudo deploy/setup-env.sh /tmp/.env
rm -f /tmp/.env /tmp/kalshi-private-key.pem
```

`setup-env.sh` rewrites local paths to server paths:

```env
KALSHI_PRIVATE_KEY_PATH=/opt/nullone/kalshi-private-key.pem
NULLONE_LOG_DIR=/opt/nullone/logs/releases
```

Verify ownership and permissions:

```bash
sudo ls -la /opt/nullone/.env /opt/nullone/kalshi-private-key.pem
sudo grep -E '^(KALSHI_PRIVATE_KEY_PATH|NULLONE_MODE|NULLONE_LOG_DIR|NULLONE_RELEASE_CAPITAL_USD|NULLONE_EXECUTION_STRATEGY)=' /opt/nullone/.env
```

Expected: files owned by `nullone:nullone`, mode `600`.

Run readiness:

```bash
sudo -u nullone ./.venv/bin/nullone ready --skip-tests
```

### 4. Timer behavior after updates

The weekly systemd timer reads `/opt/nullone/.env` each run. No restart is needed after changing `.env`.

If you change timer mode specifically, reinstall the timer:

```bash
sudo NULLONE_MODE=paper deploy/install-timer.sh
# or, only after paper telemetry is reviewed:
sudo NULLONE_MODE=live deploy/install-timer.sh
```

### Security reminders

- Never commit `.env`, `kalshi-private-key.pem`, or the EC2 SSH key PEM.
- `.gitignore` excludes `.env` and `*.pem`.
- Delete temporary copies from `/tmp/` after installation.
- Rotate the Kalshi key if the PEM is ever exposed.

---

## Step 5: Pre-flight checks

```bash
cd /opt/nullone/NullOne
sudo -u nullone ./deploy/manage.sh status
sudo -u nullone ./deploy/manage.sh simulate
sudo -u nullone ./.venv/bin/nullone ready --skip-tests
sudo -u nullone ./deploy/manage.sh dry-run
```

Fix any errors before enabling the timer.

If live Kalshi discovery fails (no markets listed yet), use a cached spec:

```bash
sudo -u nullone ./deploy/manage.sh run-now --week 2026-05-15 --no-discover --mode paper
```

---

## Step 6: Enable the weekly timer

Paper mode (default):

```bash
cd /opt/nullone/NullOne
sudo NULLONE_MODE=paper deploy/install-timer.sh
```

Live mode (only after paper telemetry looks good):

```bash
sudo NULLONE_MODE=live deploy/install-timer.sh
```

Verify:

```bash
sudo -u nullone ./deploy/manage.sh status
```

---

## Step 7: Monitor release day

**Latest JSON log:**

```bash
sudo -u nullone ./deploy/manage.sh logs
```

**Follow systemd output:**

```bash
sudo -u nullone ./deploy/manage.sh tail-logs
```

**Manual run** (holiday weeks or testing):

```bash
sudo -u nullone ./deploy/manage.sh run-now --mode paper
sudo -u nullone ./deploy/manage.sh run-now --mode paper --week 2026-05-15 --no-discover
```

---

## Day-2 operations

| Task | Command |
|---|---|
| Check timer + clock | `sudo -u nullone ./deploy/manage.sh status` |
| Readiness | `sudo -u nullone ./.venv/bin/nullone ready --skip-tests` |
| Update code | `sudo -u nullone ./deploy/manage.sh update` |
| Disable timer | `sudo -u nullone ./deploy/manage.sh timer-off` |
| Re-enable timer | `sudo -u nullone ./deploy/manage.sh timer-on` |
| Refresh Kalshi tickers | `sudo -u nullone ./deploy/manage.sh discover --week YYYY-MM-DD` |

After `update`, restart is not required — the timer invokes the venv binary on each run.

---

## Holiday releases

Most weeks the timer is enough. WNGSR holiday overrides are in `nullone/calendar/wngsr_schedule.yaml`. On odd weeks, disable the timer or run manually:

```bash
sudo -u nullone ./deploy/manage.sh run-now --mode paper
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Permission denied` on `.env` | Use `sudo -u nullone ./deploy/manage.sh ...` |
| `command not found: nullone` | Run bootstrap / `pip install -e .` in venv |
| Kalshi auth error | Check `/opt/nullone/.env` and PEM permissions (`chmod 600`) |
| Discovery fails | Kalshi may not have listed markets yet; use `--no-discover` with a valid spec |
| Clock drift | `chronyc tracking` — ensure UDP 123 outbound is allowed |
| Timer did not run | `systemctl status nullone-release.timer` and `journalctl -u nullone-release.service` |
| SSH `Permission denied (publickey)` | Wrong EC2 key path, key not chmod 400, or wrong login user |
| `scp` / `ssh` wrong IP | Use **Public IPv4**, not `172.31.x.x` |

---

## Security notes

- Do not commit `.env` or `*.pem` (already in `.gitignore`)
- EC2 security group: restrict SSH to your IP; outbound HTTPS + NTP required
- Two different `.pem` files: EC2 SSH key vs Kalshi signing key
- Prefer rotating Kalshi API keys if a PEM is ever exposed

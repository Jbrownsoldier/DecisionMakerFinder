# Deploying Decision Maker Finder on a VPS

Running this app on a VPS (Virtual Private Server) is the single biggest accuracy
improvement you can make — it costs ~$5/month and unlocks full SMTP email verification
for every company you process.

## Why a VPS?

| Feature | Home Mac / Laptop | VPS |
|---|---|---|
| SMTP port 25 | ❌ Blocked by most home ISPs | ✅ Open by default |
| SMTP email verification | ❌ Falls back to best guess | ✅ Confirms real emails |
| Always-on processing | ❌ Must keep laptop open | ✅ Runs 24/7 |
| Speed | Depends on your connection | Fast datacenter network |
| Cost | Free but limited | ~$5–6/month |

**SMTP verification covers ~70% of company domains.** Without port 25 open, that
entire capability falls back to unconfirmed guesses.

---

## Recommended VPS Providers

| Provider | Cheapest Plan | Notes |
|---|---|---|
| **Hetzner** (best value) | €4.51/month (CX22, 2 vCPU, 4GB RAM) | European datacenter, fastest |
| **DigitalOcean** | $6/month (1 vCPU, 1GB RAM) | Easy UI, good docs |
| **Vultr** | $6/month (1 vCPU, 1GB RAM) | Good performance |
| **Linode (Akamai)** | $5/month (1 vCPU, 1GB RAM) | Reliable |

All of these have **port 25 open by default**. AWS, GCP, and Azure block port 25 by
default — avoid them for this use case.

**Minimum requirements:** 1 vCPU, 1GB RAM, Ubuntu 22.04 LTS

---

## Quick Setup (Automated)

Copy `deploy.sh` to your VPS and run it as root:

```bash
# On your Mac — copy the deploy script to your VPS
scp deploy.sh root@YOUR_VPS_IP:/root/

# SSH into your VPS
ssh root@YOUR_VPS_IP

# Run the deployment script
chmod +x deploy.sh
./deploy.sh
```

---

## Manual Setup (Step by Step)

### 1. Create the VPS

Sign up at Hetzner, DigitalOcean, or Vultr. Create a server:
- **OS**: Ubuntu 22.04 LTS
- **Size**: Smallest plan (1–2 vCPU, 1–2GB RAM)
- **Add your SSH key** during setup (skip password auth)

### 2. SSH into the server

```bash
ssh root@YOUR_VPS_IP
```

### 3. Update the system and install Python

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git curl ufw
```

### 4. Create a non-root user (optional but recommended)

```bash
adduser dmf
usermod -aG sudo dmf
su - dmf
```

### 5. Upload your project files

From your Mac, upload the project to the VPS:

```bash
# On your Mac:
scp -r "/Users/jamalbrown/Desktop/JBrown AI Solutions/Coding/Claude Workflows/DecisionMakerFinder" \
    root@YOUR_VPS_IP:/opt/decision-maker-finder
```

Or use rsync for faster re-uploads:

```bash
rsync -avz --exclude '__pycache__' \
    "/Users/jamalbrown/Desktop/JBrown AI Solutions/Coding/Claude Workflows/DecisionMakerFinder/" \
    root@YOUR_VPS_IP:/opt/decision-maker-finder/
```

### 6. Install Python dependencies

```bash
cd /opt/decision-maker-finder
python3 -m venv venv
source venv/bin/activate
pip install flask requests beautifulsoup4 lxml dnspython python-whois tqdm anthropic
```

### 7. Test it runs

```bash
cd /opt/decision-maker-finder
source venv/bin/activate
python3 app.py
```

If you see `Running on http://0.0.0.0:5001` — it's working.

### 8. Open the firewall

```bash
ufw allow 22    # SSH
ufw allow 5001  # App port
ufw allow 25    # SMTP outbound (for verification) — usually open by default
ufw enable
```

### 9. Run as a background service (systemd)

Copy the service file and enable it so the app starts automatically:

```bash
cp /opt/decision-maker-finder/decision-maker-finder.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable decision-maker-finder
systemctl start decision-maker-finder
systemctl status decision-maker-finder
```

### 10. Access the app

Open in your browser:
```
http://YOUR_VPS_IP:5001
```

---

## Verify SMTP Port 25 is Open

From inside the VPS, test that port 25 outbound works:

```bash
# Try connecting to a known mail server on port 25
nc -zv gmail-smtp-in.l.google.com 25
# Expected: Connection to gmail-smtp-in.l.google.com 25 port [tcp/smtp] succeeded!

# Or test SMTP verification directly
cd /opt/decision-maker-finder
source venv/bin/activate
python3 -c "
from smtp_verify import check_smtp, detect_catch_all
from verifier import check_mx
mx = check_mx('acmeplumbing.com')  # replace with a real domain you know
print('MX:', mx)
is_ca = detect_catch_all('acmeplumbing.com', mx['mx_host'])
print('Catch-all:', is_ca)
"
```

If port 25 is blocked, contact your VPS provider. Hetzner, DigitalOcean, and Vultr
all have it open by default for new accounts.

---

## Updating the App

When you make changes on your Mac, re-sync to the VPS:

```bash
# From your Mac:
rsync -avz --exclude '__pycache__' --exclude 'venv' \
    "/Users/jamalbrown/Desktop/JBrown AI Solutions/Coding/Claude Workflows/DecisionMakerFinder/" \
    root@YOUR_VPS_IP:/opt/decision-maker-finder/

# Then on the VPS, restart the service:
ssh root@YOUR_VPS_IP "systemctl restart decision-maker-finder"
```

---

## Security Notes

- The app is not HTTPS by default. For HTTPS, add Nginx + Certbot (Let's Encrypt).
- The app is accessible to anyone who knows your IP. Consider UFW restricting port 5001
  to your home IP only: `ufw allow from YOUR_HOME_IP to any port 5001`
- Never commit your Anthropic API key to git — enter it in the UI each time instead.

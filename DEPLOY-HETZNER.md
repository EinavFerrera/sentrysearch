# Deploy Optimus Vision on Hetzner (production)

This guide walks through deploying the app on a **Hetzner Cloud** VPS: Docker, persistent data, **HTTPS** with Caddy, and optional **SSO**. Follow the steps in order.

## What you need before you start

| Item | Purpose |
|------|---------|
| **Hetzner account** | [Cloud Console](https://console.hetzner.cloud/) |
| **Domain name** | For HTTPS and stable URLs (e.g. `vision.example.com`) |
| **DNS access** | To create an **A record** pointing at the server |
| **`GEMINI_API_KEY`** | [Google AI Studio](https://aistudio.google.com/apikey) — required for indexing and search |
| **SSH key** | Added to the server for login |

Optional for sign-in with Google (or another OIDC provider):

- OAuth client id, secret, and issuer URL  
- Decide the **bootstrap admin email** (first admin when the user database is empty)

## Server sizing (quick pick)

| Plan | When to use |
|------|-------------|
| **CX22** (2 vCPU, 4 GB) | Lowest cost; fine for light use with the **Gemini** backend. |
| **CX32** (4 vCPU, 8 GB) | Better if several people index long videos or you want headroom. |

Pick a **region** close to your users. You do **not** need a GPU for the default Gemini stack. Start with **40–80 GB** disk; ChromaDB, uploads, and clips grow with usage.

---

## Step 1 — Create the server

1. In Hetzner Cloud → **Add Server**.
2. **Image:** Ubuntu **24.04** (or 22.04).
3. **Type:** CX22 or CX32 as above.
4. **Volume (recommended):** attach a volume (e.g. 40–160 GB) if you want data off the root disk; mount it later at `/mnt/optimus-data` (see Step 6).
5. **Firewall (Hetzner Cloud):** allow **SSH (22)** from your IP. You can allow **80** and **443** here too, or use UFW on the host in Step 9.
6. **SSH key:** select your public key.

Note the server **IPv4** address for DNS and SSH.

---

## Step 2 — Point DNS at the server

1. At your DNS host, create an **A record**:  
   `vision.example.com` → `YOUR_SERVER_IP`  
   (use your real hostname.)
2. Wait until it resolves (often a few minutes; TTL may delay longer).

HTTPS and OAuth **redirect URIs** must use this **exact** hostname.

---

## Step 3 — Install Docker on the VPS

```bash
ssh root@YOUR_SERVER_IP

apt update && apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${VERSION_CODENAME:-stable}") stable" \
  > /etc/apt/sources.list.d/docker.list

apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

---

## Step 4 — Get the application onto the server

**Option A — clone from Git (typical)**

```bash
cd /opt
git clone https://github.com/YOUR_ORG/sentrysearch.git
cd sentrysearch
```

**Option B — copy the repo** with `rsync` or `scp` if the project is private and you prefer not to clone from GitHub on the server.

---

## Step 5 — Create production `.env`

```bash
cd /opt/sentrysearch   # or your path
cp .env.example .env
chmod 600 .env
nano .env
```

### Minimum for production (no SSO yet)

```bash
GEMINI_API_KEY=your-key-here

# Strong random secret (generate with: openssl rand -hex 32)
OPTIMUS_SESSION_SECRET=

# After HTTPS is working (Step 8), set:
OPTIMUS_SESSION_HTTPS_ONLY=true

# Optional: host port; app inside the container is always 7778
OPTIMUS_PORT=7778
```

### Recommended behind Caddy (HTTPS + correct OAuth redirect)

When TLS terminates at **Caddy** on the host and Docker only listens on **localhost**, set the **public** callback URL and trust proxy headers so the app builds correct URLs:

```bash
# Public site URL used in the browser (no trailing slash on issuer)
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...

# Must match Google Cloud → Credentials → Authorized redirect URIs EXACTLY
OIDC_REDIRECT_URI=https://vision.example.com/auth/callback

# First login creates this user as admin if the DB has no users yet
OIDC_BOOTSTRAP_ADMIN_EMAIL=you@yourdomain.com

TRUST_PROXY=1
FORWARDED_ALLOW_IPS=*
```

**Security:** `FORWARDED_ALLOW_IPS=*` is acceptable when the container port is bound to **127.0.0.1** only (Step 7) so only the host’s reverse proxy can reach the app. Tighten to specific subnets if your layout differs.

**Google OAuth:** In [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **Credentials** → your OAuth client:

- **Authorized JavaScript origins:** `https://vision.example.com`
- **Authorized redirect URIs:** exactly `https://vision.example.com/auth/callback` (same string as `OIDC_REDIRECT_URI`)

---

## Step 6 — (Optional) Use a Hetzner volume for `/data`

If you attached a volume at `/mnt/optimus-data`:

```bash
mkdir -p /mnt/optimus-data/optimus
```

Edit `docker-compose.yml` and replace the named volume with a bind mount:

```yaml
    volumes:
      - /mnt/optimus-data/optimus:/data
```

Remove or comment out the bottom `volumes:` block’s `optimus_data` entry if you no longer use the named volume.

---

## Step 7 — Bind the app to localhost (production)

So the UI is **not** exposed on the public internet over plain HTTP, publish the container port only on the loopback interface.

Edit `docker-compose.yml` **ports** line:

```yaml
    ports:
      - "127.0.0.1:${OPTIMUS_PORT:-7778}:7778"
```

Traffic from the internet should go **443 → Caddy → 127.0.0.1:7778**.

---

## Step 8 — Build and start the stack

```bash
cd /opt/sentrysearch
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=50
```

Check health:

```bash
curl -fsS http://127.0.0.1:7778/api/health
```

You should see `{"status":"ok"}`.

---

## Step 9 — Caddy for HTTPS (Let’s Encrypt)

Install Caddy:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

Edit `/etc/caddy/Caddyfile` (replace the hostname):

```caddy
vision.example.com {
    reverse_proxy 127.0.0.1:7778
}
```

Reload Caddy:

```bash
systemctl reload caddy
```

Open `https://vision.example.com` in a browser. Once HTTPS works, set in `.env`:

```bash
OPTIMUS_SESSION_HTTPS_ONLY=true
```

Then:

```bash
docker compose up -d
```

---

## Step 10 — Host firewall (UFW)

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
ufw status
```

Do **not** expose port **7778** publicly if you followed Step 7.

---

## Step 11 — SSO and users (optional)

1. With **SSO still off**, open **Admin** in the web UI (when auth is disabled you still have admin access).
2. Confirm **Allowed redirect URI** matches what you entered in Google (Admin panel shows the value the server uses).
3. Add users and roles (**viewer** / **user** / **admin**).
4. Turn on **Require SSO** only after OIDC env vars are set and the redirect URI is verified.

If the database is empty and you set `OIDC_BOOTSTRAP_ADMIN_EMAIL`, the first successful login with that email becomes **admin**.

---

## Step 12 — Ongoing operations

| Task | Command / note |
|------|----------------|
| **Logs** | `docker compose logs -f` |
| **Restart** | `docker compose restart` |
| **Update app** | `git pull && docker compose up -d --build` |
| **Backup** | Snapshot the Hetzner volume or copy the data directory. Include at least `db/`, `uploads/`, `clips/`, and **`auth.db`** (under `/data` if you use SSO). |

---

## Environment variables reference

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | **Yes** | Gemini API key. |
| `SENTRYSEARCH_DATA_DIR` | Auto in Docker | `/data` in the image; all state under that path. |
| `OPTIMUS_PORT` | No | Host port mapped into the container (default `7778`). |
| `OPTIMUS_SESSION_SECRET` | **Yes (prod)** | Random secret for session cookies. |
| `OPTIMUS_SESSION_HTTPS_ONLY` | **Yes (prod)** | Set `true` when only HTTPS is used. |
| `OIDC_ISSUER` | For SSO | Issuer URL, no trailing slash (e.g. `https://accounts.google.com`). |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | For SSO | From your IdP. |
| `OIDC_REDIRECT_URI` | **Strongly recommended** behind a proxy | `https://your-domain/auth/callback` — must match IdP exactly. |
| `OIDC_BOOTSTRAP_ADMIN_EMAIL` | No | First admin when user table is empty. |
| `TRUST_PROXY` | Behind Caddy/nginx | Set `1` so `X-Forwarded-*` is honored (with `FORWARDED_ALLOW_IPS`). |
| `FORWARDED_ALLOW_IPS` | With `TRUST_PROXY` | Often `*` in Docker when the app is only on `127.0.0.1`. |

---

## Production checklist

- [ ] DNS **A record** points to the server.
- [ ] `.env` has `GEMINI_API_KEY` and `OPTIMUS_SESSION_SECRET`.
- [ ] Container **ports** bound to `127.0.0.1`, not `0.0.0.0`.
- [ ] **Caddy** (or nginx) serves **HTTPS**; `OPTIMUS_SESSION_HTTPS_ONLY=true`.
- [ ] **UFW** allows 22, 80, 443; blocks public **7778**.
- [ ] **OAuth** redirect URI in Google matches `OIDC_REDIRECT_URI` exactly.
- [ ] `.env` is **not** in git; `chmod 600 .env`.

---

## Optional: run without Docker

Install dependencies on the server: `uv sync --extra web`, set `SENTRYSEARCH_DATA_DIR` to a persistent directory, load the same environment variables, and run:

```bash
sentrysearch serve --host 0.0.0.0 --port 7778
```

Use **systemd** to supervise the process. Prefer Docker for simpler upgrades and consistent dependencies.

---

## Troubleshooting

- **`redirect_uri_mismatch` (Google):** The **Authorized redirect URI** in Google must match `OIDC_REDIRECT_URI` and the **Admin** panel “Allowed redirect URI” line character-for-character (including `https` and no trailing slash unless you intentionally use one).
- **429 / Gemini quota:** Reduce indexing load or upgrade the API plan.
- **Disk full:** Expand the volume or remove old uploads from the Library tab.
- **Video preview broken:** Source files must still exist at the paths stored in the index; moving files breaks preview until you re-index.

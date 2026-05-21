# NekoTunnel Central

FastAPI + PostgreSQL control panel for managing Railway FRP/SSLH tunnel slots.

## What this includes

- Admin dashboard for Railway API tokens, slots, users, and sessions.
- PostgreSQL auto table creation at startup.
- Encrypted Railway API tokens and FRP slot tokens.
- `/api/connect`, `/api/heartbeat`, `/api/disconnect` for clients.
- Railway CLI-based slot creation/deploy scaffold.
- Render Docker deployment files.

## Required Render environment variables

Render will provide `DATABASE_URL` automatically if you deploy with the included `render.yaml` blueprint. Set or generate these:

```txt
APP_SECRET=long_random_string
ENCRYPTION_KEY=long_random_string
ADMIN_TOKEN=long_random_admin_password
SESSION_TTL_SECONDS=90
CLEANUP_INTERVAL_SECONDS=30
```

Keep `ENCRYPTION_KEY` permanent. If it changes, old encrypted Railway/FRP tokens cannot be decrypted.

## Deploy from GitHub to Render

1. Create a GitHub repo and push this project.
2. In Render, create a Blueprint from the repo, or create a Web Service using Docker and attach a Render PostgreSQL database.
3. Add the environment variables above.
4. Open the Render URL and login with `ADMIN_TOKEN`.

## First setup after deploy

1. Go to **Railway Accounts** and add a workspace/account API token.
2. Go to **Users** and create a user token. Copy it immediately.
3. Go to **Slots**.
   - Either add a manual slot from an already working Railway project.
   - Or use **Create Railway Slot** to create/deploy a new Railway project.
4. If the slot is `tcp_pending`, open that Railway service and enable TCP Proxy with internal port `8080`, then click **Refresh TCP**.
5. Download the client from `/client/nekotunnel` or use the included `client/nekotunnel` file.

## Client usage

```bash
chmod +x nekotunnel
./nekotunnel tcp 22 USER_TOKEN https://your-render-app.onrender.com
```

Or:

```bash
export NEKOTUNNEL_TOKEN="USER_TOKEN"
export NEKOTUNNEL_API_URL="https://your-render-app.onrender.com"
./nekotunnel tcp 22
```

## Notes

- Railway TCP Proxy must be enabled with internal port `8080` for every slot.
- The first version uses Railway CLI from inside the Docker container. If a CLI operation fails, add manual slots or refresh after enabling TCP Proxy.
- This is a starter control plane; production hardening should add stronger admin auth, role-based access, HTTPS-only cookies, rate limits, and backups.

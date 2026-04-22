# Deploying Ghost Approvals

The bot is a single always-on Python worker. Railway.app's free $5/month credit
is enough for v1.

## 1. Push the repo to GitHub
```
git add .
git commit -m "feat: initial Ghost Approvals bot"
git push origin main
```

## 2. Create the Railway project
1. Go to https://railway.app/new
2. "Deploy from GitHub repo" → pick `setzerr55/ghost-approvals`
3. Railway auto-detects Python via `requirements.txt` + `runtime.txt`.
4. In the **Variables** tab, add:
   - `TELEGRAM_BOT_TOKEN`
   - `ALCHEMY_API_KEY`
   - `GROQ_API_KEY`
   - (optional) `PUBLIC_BASE_URL`, `LOG_LEVEL`
5. In **Settings → Start Command** verify it's `python -m ghost_approvals.main`.
6. Add a **Volume** mounted at `/app/data` so the SQLite database survives
   restarts:
   - Volume name: `ghost-data`, mount path: `/app/data`
   - Then set variable `DB_PATH=/app/data/ghost.db`.
7. Deploy.

## 3. Verify the bot
Open Telegram, find `@ghost_approvals_bot` (or whatever username you chose at
BotFather), send `/start`. You should see the welcome text. Then:
```
/scan 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045   # vitalik.eth
```
You should get a scan within ~60 seconds.

## 4. Logs
Railway → your project → Deployments → latest → "View Logs". Look for
`Ghost Approvals bot is live.` on startup.

## 5. Updating
Just `git push` — Railway auto-redeploys on push to `main`.

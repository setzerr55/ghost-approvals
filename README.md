# Ghost Approvals

> Your wallet's personal security analyst. Weekly reports in your Telegram. Sleep well.

Ghost Approvals scans your Ethereum / Base / Arbitrum / Optimism / Polygon / BNB wallet for forgotten ERC-20 token approvals that could drain your funds if the approved contract got exploited — then explains each one in plain English using AI, gives you a **Security Score**, and offers one-click batch revoke.

## Why this exists

- **40%+ of wallet drains in 2024–2025 came through forgotten token approvals.**
- Existing tools (revoke.cash, Etherscan) are passive: you have to remember to visit them.
- Ghost Approvals is **proactive**: weekly Telegram digest, AI explanations, drain simulation.

## Features (v1)

- `/scan <address>` — one-shot audit of all active token approvals across 6 EVM chains
- **Drain Simulation** — for each approval, shows how much $USD you would lose if that contract got hacked right now
- **Security Score (0–100)** — aggregated risk score with shareable image
- **AI Explanations** — every approval explained in 2–3 sentences by Llama 3.3 via Groq (free tier)
- `/monitor <address>` — weekly auto-scan with digest sent to your Telegram
- **Batch Revoke** — deep-link to your wallet with a Multicall3 transaction that revokes all risky approvals in one signature
- **No wallet-connect. Paste-address only.** We never touch your private keys.

## Quickstart (local dev)

```bash
git clone https://github.com/setzerr55/ghost-approvals.git
cd ghost-approvals
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — fill in TELEGRAM_BOT_TOKEN, ALCHEMY_API_KEY, GROQ_API_KEY
python -m ghost_approvals.main
```

## Deployment

See [DEPLOY.md](DEPLOY.md) for one-click Railway deployment.

## Tech

- Python 3.11+
- `python-telegram-bot` v21 (async)
- Alchemy (RPC + logs)
- Groq (Llama 3.3 70B) for AI explanations
- SQLite for caching + monitored wallets
- APScheduler for weekly cron

## License

MIT.

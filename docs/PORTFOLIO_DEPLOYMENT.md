# Deploying Scenarai to Render

> **Alternative hosting guide:** the current portfolio path is Vercel. Follow
> [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) for the configured Vite + FastAPI services using the
> existing Neon and Upstash resources. This file describes a separate Render Blueprint and is not
> needed to create or update the Vercel deployment.

This repository includes a Render Blueprint and a single-service Docker image for the text-only portfolio app. The frontend is served by FastAPI from the same origin; an ARQ worker runs beside the API. PostgreSQL and Redis are external managed services so data does not live on the app container's temporary disk.

## Free portfolio setup

The app service uses Render's free web plan, so it sleeps after 15 minutes without inbound traffic and takes roughly a minute to wake. Render free Postgres expires after 30 days and free Key Value loses data on restart, so this guide uses Neon Postgres and Upstash Redis instead. The free Render web instance has 512 MB RAM; if the combined API and worker exceed that, the service can restart. This is suitable for a small portfolio demo, not production.

### 1. Create Neon Postgres

1. Create a Neon account and a project on its Free plan.
2. Create/select the project database, then copy its connection string. Use the direct connection string, not a pooled endpoint.
3. Keep the password private. The URL may contain `sslmode=require`; the app adapts this to its asyncpg driver.

Neon currently lists its Free plan as $0 with no credit card, up to 1 GB storage and 100 compute-hours per project monthly. Its compute sleeps when idle; the first database operation can be slower while it wakes.

### 2. Create Upstash Redis

1. Create a Redis database on Upstash's Free plan.
2. Copy the TLS connection URI (`rediss://...`) from the Connect panel. Do not use the REST URL/token.
3. Keep this URL private. ARQ uses Redis for scenario preparation jobs and session state.

Upstash currently lists a free allowance of 256 MB, 10 GB monthly bandwidth, and 500,000 commands. Free-tier limits can change.

### 3. Deploy the Render Blueprint

1. Push the deployment files in this repository to GitHub.
2. In Render, choose **New → Blueprint**, connect the Scenarai repository, and apply the root `render.yaml`.
3. Set the prompted `DATABASE_URL` to the Neon URL, `REDIS_URL` to the Upstash `rediss://` URL, and `GROQ_API_KEY` to the existing key from your private local `.env`.
4. Render generates `JWT_SECRET`. Leave `MEDIA_ENABLED=false`.
5. To enable alerts, add `ALERT_WEBHOOK_URL` in the Render service's environment settings and set it to your incoming webhook URL. Optionally set `ALERT_WEBHOOK_FORMAT` to `discord`, `slack`, or `generic` (default). Keep the URL private.
6. Start the first deploy and watch the deploy logs. The container runs `alembic upgrade head`, seeds a published, ready-to-play portfolio scenario, then starts the API and worker. The seed is safe to run on every startup and does not call the language model.
7. When the service is live, open its `https://….onrender.com` URL.

Never commit `.env` or paste secrets into GitHub, a chat, or a public issue. Add them only in Render's secret environment-variable fields. The public app uses your Groq key for visitors' turns; check your Groq account's limits and usage.

## What the deployment runs

- One Render web service builds `Dockerfile.render`, serves the production React bundle and FastAPI API from one origin, and runs one ARQ worker process.
- Neon stores application records. Its free compute scales to zero when idle.
- Upstash provides Redis to the API and worker. Session state is temporary by design; Redis restarts or free-tier limits can interrupt active sessions/jobs.
- Generated image/audio/video features stay off (`MEDIA_ENABLED=false`).
- When a webhook is configured, alerts cover startup/process failures, unhandled API errors, failed scenario/session preparation, and high or exhausted Groq per-minute capacity. Alerts are cooldown-limited and omit prompts, user content, and secret values.

The Render URL remains the same when the free service sleeps, but a visitor's first request after inactivity waits for it to wake. Render may restart free instances and has no uptime guarantee on this tier.

## Update or remove it

After pushing code changes to the configured branch, Render rebuilds and deploys automatically. To stop the public demo, suspend or delete the Render service in its dashboard. Neon and Upstash are separate resources; delete them too if you no longer need the stored data or queue.

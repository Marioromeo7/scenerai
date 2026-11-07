# Vercel portfolio deployment

This setup deploys the Vite app and FastAPI API together through Vercel Services. Neon supplies PostgreSQL and Upstash supplies Redis. It is intended for a small public portfolio demo, not a production service. Vercel Services is currently in beta, so enable the Services framework in the Vercel project settings. Vercel Hobby is the free personal plan; leave the project on Hobby and do not start a Pro trial or add a paid plan.

The deployment uses `SERVERLESS_MODE=true`: the app does not start an ARQ worker. It can play published, prefab-ready scenarios, including the seeded “The Quiet Frequency” scenario. Session initialization runs inline using the saved prefab. Creating or publishing scenarios, draft previews, and generated media are disabled for this hosting mode. Each visitor still needs to create an account and persona.

## 1. Create the free backing services

1. Create a free Neon Postgres project. Copy the connection string for the database. Add `sslmode=require` if Neon provides it. Keep it private.
2. Create a free Upstash Redis database. Copy its TLS `rediss://` connection URL, not the REST URL or token.
3. Keep both URLs ready for Vercel's environment-variable form. Free-plan availability and quotas can change.

## 2. Create the Vercel project

1. Sign in to Vercel and choose **Add New → Project**.
2. Import `Marioromeo7/scenerai` from GitHub.
3. Leave the Root Directory set to the repository root (`.`). Do not select only `frontend/app`; `vercel.json` builds both services.
4. In the project’s **Settings → Build and Deployment**, set the Framework Preset to **Services**. Vercel’s services feature is in beta and this setting is required.
5. Add the environment variables below under **Settings → Environment Variables**. Select **Production** (and **Preview** too if you want preview deployments to work). Add them before the first deploy so the backend build can migrate and seed the database.
6. Save and deploy.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Neon pooled Postgres connection URL (`-pooler` host), for application requests |
| `DATABASE_URL_UNPOOLED` | Neon direct Postgres URL, for Alembic migrations during build |
| `REDIS_URL` | Upstash TLS Redis connection URL (`rediss://…`) |
| `JWT_SECRET` | A newly generated, long random secret (at least 32 random bytes) |
| `GROQ_API_KEY` | Your Groq API key; keep it server-side and never use a `VITE_` prefix |
| `SERVERLESS_MODE` | `true` |
| `MEDIA_ENABLED` | `false` |
| `VITE_PORTFOLIO_MODE` | `true` |
| `VITE_MEDIA_ENABLED` | `false` |

`ALERT_WEBHOOK_URL` and `ALERT_WEBHOOK_FORMAT` are optional. Add the webhook URL only as a Vercel secret environment variable if you want the existing alerts. Do not put secrets in the repository or frontend variables.

## 3. Verify the live link

After the first deployment completes, open the generated `https://…vercel.app` URL. Check `https://…vercel.app/api/health`, then create an account, create a persona, and play the seeded scenario. The backend build runs Alembic migrations and the idempotent demo seeder. Check the Vercel deployment build logs if either step fails.

The app makes Groq requests on behalf of public visitors, so set account limits and monitor usage. Vercel Hobby is intended for personal, non-commercial use, and functions have platform time and resource limits; this is a portfolio demo configuration. Free database and Redis tiers also have quotas and may sleep or be interrupted.

## Update or remove the deployment

Once Git integration is connected, pushes to the selected production branch trigger a new deployment. To take the demo offline, delete or disable the Vercel project. Delete the Neon and Upstash resources separately if you no longer need them or the stored data.

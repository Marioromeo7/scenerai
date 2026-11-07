# Vercel portfolio deployment

This repository is configured as a Vercel Services project: the Vite frontend serves `/`, and the
FastAPI backend serves `/api`. The free Hobby plan is for personal projects; leave the project on
Hobby and do not start a Pro trial or add a paid plan. Vercel Services may require beta access in
your account; the project import should show **Configuration defined in vercel.json** when it has
recognized the service map. See [Vercel Services](https://vercel.com/docs/services) and the
[Hobby plan](https://vercel.com/docs/plans/hobby).

## Connected services

The backing services for this project are already provisioned:

- Neon Postgres project `floral-cake-92062204`, branch `production`.
- Upstash Redis database `scenerai-portfolio`, primary region `us-east-1` (AWS US East 1), Free
  plan.

Connection strings and tokens belong in Vercel Environment Variables, never in GitHub. The local
`.env` also contains Docker-local database and Redis credentials and other development settings;
do not import the whole file without removing those unrelated entries.

## Vercel project configuration

Import `Marioromeo7/scenerai` on the `main` branch. Use the repository root (`.`), the **Services**
framework, and the existing service map in `vercel.json`. It routes the backend to `/api` and the
frontend to `/`. Do not import `imagepipe` or `loadtest` as deployed services.

Add these variables to the project's **Production** environment before triggering the first
production build:

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Neon pooled connection URL for app requests |
| `DATABASE_URL_UNPOOLED` | Neon direct connection URL for build-time Alembic migrations |
| `REDIS_URL` | Upstash TLS TCP URL (`rediss://…`); the FastAPI backend uses this variable |
| `JWT_SECRET` | Long random secret, at least 32 random bytes |
| `GROQ_API_KEY` | Groq API key, server-side only |
| `SERVERLESS_MODE` | `true` |
| `MEDIA_ENABLED` | `false` |
| `VITE_PORTFOLIO_MODE` | `true` |
| `VITE_MEDIA_ENABLED` | `false` |

`ALERT_WEBHOOK_URL` and `ALERT_WEBHOOK_FORMAT` are optional; add them only if you want alerts
delivered to the configured webhook. Use the existing format value from your private `.env` or
`generic`. Do not add `UPSTASH_REDIS_REST_URL` or `UPSTASH_REDIS_REST_TOKEN` for this Python
deployment; the backend uses `REDIS_URL`.

If you import `.env` through Vercel’s form, remove local-only entries such as `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `NEON_BRANCH*`, and the unused Upstash REST
credentials before deploying. Never commit `.env` or paste its values into GitHub.

## Trigger the production build

Vercel creates a production deployment from a push to the configured production branch (`main`).
If the project page says the domain is configured but there is no Production deployment, confirm
the Production variables above are saved, then push a new commit to `main`. A commit made before the
Vercel project was connected does not trigger a build retroactively. The new push will trigger it
automatically.

The backend build applies Alembic migrations using `DATABASE_URL_UNPOOLED`, then runs the
idempotent demo seeder using `DATABASE_URL`. It inserts the prefab-ready public scenario “The Quiet
Frequency” without making a Groq call. `SERVERLESS_MODE=true` disables the persistent ARQ worker;
published prefab-ready scenarios can be played inline, while scenario creation, publishing, draft
previews, and generated media are disabled in this portfolio profile.

## Verify the live link

When Vercel reports the deployment **Ready**, open its generated `https://…vercel.app` URL and
check `/api/health`. Then create an account and persona and play “The Quiet Frequency”. Visitors'
turns use the configured Groq key and its quota. Neon, Upstash, and Vercel also have free-tier
limits.

## Update or remove the deployment

Later pushes to `main` create new production deployments. To take the demo offline, disable or
delete the Vercel project. Delete the Neon and Upstash resources separately if you no longer need
their stored data.

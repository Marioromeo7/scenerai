FROM node:20-alpine
WORKDIR /app
# package-lock.json wasn't being copied here -- `npm install` ran with only
# package.json present in the build context at that point, so it couldn't
# use the lockfile at all despite one being committed to the repo. Builds
# weren't reproducible: two `docker compose build frontend` runs at
# different times could resolve different transitive dependency versions
# within package.json's semver ranges, silently diverging from whatever
# was actually tested. `npm ci` (not `npm install`) enforces that
# package.json and package-lock.json agree, failing loudly instead of
# silently re-resolving if they ever drift.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

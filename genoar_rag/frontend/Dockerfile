# Two stages, and the second one is the only one that ships.
#
# A single stage cannot get rid of anything. Layers are additive: installing
# four hundred megabytes of build tools and then deleting them leaves both the
# installing and the deleting in the image, and it weighs what the larger of the
# two weighed. The only way to not carry something is to build it somewhere else
# and copy across what came out.
FROM node:22-slim AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

# The image has to build the site, not stop at this copy and run `next dev` over
# a mounted host directory. A dev server serves unminified bundles and source
# maps, compiles each route on its first request, and says so in a badge in the
# corner. It also turns a deploy into deleting .next by hand, because it hands
# back what it compiled last and the source changing underneath it is not always
# enough to convince it otherwise.
COPY . .

# Where the backend is, and it has to be known here rather than at start.
#
# next.config.mjs rewrites /api and /health to it, and Next resolves that while
# building: the destination is written into routes-manifest.json and `next
# start` reads the manifest without ever calling rewrites() again. Setting
# API_URL on the running container therefore does nothing at all — the pages
# come up and every call to the backend answers 500 against a localhost that
# has nothing on it. Which is what happened, and only showed by running it.
#
# The default is the compose service name because that is what both deployments
# call it. docker-compose.yml passes it as a build argument so that the one
# place it takes effect is the place it is written.
ARG API_URL=http://backend:8000
ENV API_URL=$API_URL
# Which dataset the dashboard describes. Baked in at build time, like API_URL:
# Next reads NEXT_PUBLIC_* when it compiles. Empty means the same one the
# search page uses, which is every single-dataset deployment.
ARG NEXT_PUBLIC_DASHBOARD_DATASET=
ENV NEXT_PUBLIC_DASHBOARD_DATASET=$NEXT_PUBLIC_DASHBOARD_DATASET
RUN npm run build


FROM node:22-slim AS serve

WORKDIR /app
ENV NODE_ENV=production

# next, react and react-dom. TypeScript, Tailwind, jest and eslint built the
# site in the stage above and have no part in serving it.
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev && npm cache clean --force

# The build, the files served beside it, and the config — which Next reads again
# at start, not only at build.
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.mjs ./next.config.mjs

EXPOSE 3000

# -H, because a server bound to localhost inside a container is a server nobody
# outside it can reach.
CMD ["npx", "next", "start", "-H", "0.0.0.0"]

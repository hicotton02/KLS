FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    HOME=/tmp

WORKDIR /app

COPY --from=build /app/dist/standalone ./

EXPOSE 3000

USER 10001:10001

CMD ["node", "server.js"]

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_DISABLE_SSE=true
ARG VITE_MEDIA_ENABLED=false
ENV VITE_DISABLE_SSE=$VITE_DISABLE_SSE VITE_MEDIA_ENABLED=$VITE_MEDIA_ENABLED
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
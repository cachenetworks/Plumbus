FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=10 CMD wget -q -O /dev/null http://localhost/health || exit 1

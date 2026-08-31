FROM nginx:1.27-alpine
COPY default.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=10 CMD wget -q -O /dev/null http://localhost/health || exit 1

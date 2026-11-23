FROM node:26-trixie AS assets
WORKDIR /build
COPY public-viewer/package*.json ./
RUN npm ci --ignore-scripts
COPY public-viewer/build.mjs ./
COPY public-viewer/src ./src
RUN npm run build

FROM python:3.13-slim
ENV HOME=/tmp
WORKDIR /app
COPY public-viewer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY public-viewer/app.py .
COPY --from=assets /build/dist ./dist
USER 10001:10001
EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/health', timeout=2).read()"
CMD ["gunicorn", "--bind", "0.0.0.0:8050", "--workers", "2", "--threads", "2", "--timeout", "30", "--worker-tmp-dir", "/tmp", "--access-logfile", "/dev/null", "app:server"]

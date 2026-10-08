FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SEALBOARD_HOST=0.0.0.0 SEALBOARD_PORT=8080 SEALBOARD_DATA=/data

WORKDIR /app
# dependencies first, so editing the app does not re-download them
RUN pip install --no-cache-dir "flask>=2.3" "waitress>=3.0.1" \
 && useradd --system --uid 10001 --home /data sealboard \
 && mkdir -p /data && chown sealboard /data
COPY pyproject.toml README.md LICENSE ./
COPY sealboard ./sealboard
RUN pip install --no-cache-dir --no-deps .

USER sealboard
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/vault' % os.environ['SEALBOARD_PORT'], timeout=2)"
CMD ["sealboard"]

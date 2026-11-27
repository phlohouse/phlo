# Notebook kernel host: runs Observatory notebook kernels, one Unix user per
# kernel, with no network for kernel users. See notebook_gateway.py.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get upgrade --no-install-recommends --yes \
    && apt-get install -y --no-install-recommends ca-certificates iptables \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/phlo-kernels
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
    && python -m compileall -q /usr/local/lib/python3.12

COPY notebook_gateway.py ./notebook_gateway.py
COPY entrypoint.sh ./entrypoint.sh
RUN chmod 0755 ./entrypoint.sh && chmod 0644 ./notebook_gateway.py \
    && mkdir -p /var/lib/phlo-kernels

EXPOSE 8890
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8890/health', timeout=4)"
ENTRYPOINT ["/opt/phlo-kernels/entrypoint.sh"]

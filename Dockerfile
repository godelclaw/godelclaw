FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git pkg-config python3-dev python3-venv ca-certificates \
    libgmp-dev libcurl4-openssl-dev swi-prolog-nox \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/godelclaw
COPY . .
RUN python3 scripts/build_cetta.py --prefix /opt/cetta \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
       -r modes/iter/requirements.txt -r modes/omega/requirements.txt \
    && rm -rf .build \
    && useradd --create-home --uid 10001 agent \
    && mkdir -p /state && chown agent:agent /state

ENV PATH="/opt/venv/bin:/opt/cetta:${PATH}" \
    CETTA_BIN="/opt/cetta/cetta" \
    PYTHONPATH="/opt/venv/lib/python3.12/site-packages" \
    XDG_STATE_HOME="/state" \
    PYTHONDONTWRITEBYTECODE="1"
USER agent
VOLUME ["/state"]
ENTRYPOINT ["./run.sh", "--mode"]
CMD ["iter"]

FROM influxdb:3-enterprise

USER root

RUN apt-get update && apt-get install -y --no-install-recommends jq python3 \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
COPY compat_proxy.py /compat_proxy.py
RUN chmod +x /run.sh

CMD ["/run.sh"]

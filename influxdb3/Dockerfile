# Pinned to an exact release on purpose. The floating 3-enterprise tag already points at 3.12,
# and the first start on 3.12 rewrites the catalog so 3.11.x can no longer read it. Moving
# to 3.12 must be a planned step with a catalog backup, never a side effect of a rebuild.
FROM influxdb:3.11.5-enterprise

USER root

RUN apt-get update && apt-get install -y --no-install-recommends jq python3 \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
COPY compat_proxy.py /compat_proxy.py
RUN chmod +x /run.sh

CMD ["/run.sh"]

# Pinned to an exact release on purpose; never use the floating 3-enterprise tag. The
# first start on 3.12 rewrites the catalog so 3.11.x can no longer read it, so a version
# change must be a planned step with a catalog backup, never a side effect of a rebuild.
FROM influxdb:3.12.1-enterprise

USER root

RUN apt-get update && apt-get install -y --no-install-recommends jq python3 \
    && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
COPY compat_proxy.py /compat_proxy.py
RUN chmod +x /run.sh

CMD ["/run.sh"]

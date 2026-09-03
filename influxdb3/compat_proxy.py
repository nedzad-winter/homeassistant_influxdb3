#!/usr/bin/env python3
"""
Thin compatibility shim in front of InfluxDB 3 Core.

Home Assistant's influxdb integration validates connectivity (both in the
config-flow wizard and on every startup of the integration) by sending an
intentionally EMPTY write. Real InfluxDB 1.x silently accepts an empty write
(204). InfluxDB 3 Core rejects it with 400 "incoming write was empty", which
HA treats as a hard connection failure and refuses to set up the integration
at all.

This proxy sits in front of the real server: any write request with an empty
body gets an immediate 204 without being forwarded (mimicking 1.x), while
every other request - real writes, queries, health checks - is passed
through to the backend unchanged.
"""
import http.server
import urllib.request
import urllib.error
import sys

BACKEND = "http://127.0.0.1:8181"
LISTEN_PORT = 8080

WRITE_PATHS = ("/write", "/api/v2/write")


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("compat_proxy: " + (fmt % args) + "\n")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _is_write_path(self):
        path_only = self.path.split("?", 1)[0]
        return path_only in WRITE_PATHS

    def _forward(self, method, body):
        url = BACKEND + self.path
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in self.headers.items():
            if key.lower() in ("host", "content-length", "connection"):
                continue
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._send_response(resp.status, resp.getheaders(), resp.read())
        except urllib.error.HTTPError as e:
            self._send_response(e.code, e.headers.items(), e.read())
        except Exception as e:
            body = str(e).encode()
            self._send_response(502, [("Content-Type", "text/plain")], body)

    def _send_response(self, status, headers, body):
        self.send_response(status)
        for key, value in headers:
            if key.lower() in ("transfer-encoding", "connection"):
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        body = self._read_body()
        # influxdb-python's make_lines([]) returns "\n" (not ""), so a
        # write with zero real points is a single newline, not zero bytes.
        if self._is_write_path() and len(body.strip()) == 0:
            self._send_response(204, [], b"")
            return
        self._forward("POST", body)

    def do_GET(self):
        self._forward("GET", None)

    def do_PUT(self):
        body = self._read_body()
        self._forward("PUT", body)

    def do_DELETE(self):
        self._forward("DELETE", None)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), ProxyHandler)
    print(f"compat_proxy listening on :{LISTEN_PORT}, forwarding to {BACKEND}")
    server.serve_forever()

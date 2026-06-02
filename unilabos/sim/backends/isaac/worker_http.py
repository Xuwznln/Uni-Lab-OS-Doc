from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

from unilabos.sim.backends.isaac.protocol import decode_request, encode_error, encode_response


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(worker_state: Any):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._write_json(worker_state.health(), status=200)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/rpc":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                op, args = decode_request(self.rfile.read(length))
                self._write_body(encode_response(worker_state.dispatch(op, args)), status=200)
            except Exception as exc:
                self._write_body(encode_error(str(exc)), status=500)

        def _write_json(self, payload: dict[str, Any], status: int) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._write_body(body, status=status)

        def _write_body(self, body: bytes, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler

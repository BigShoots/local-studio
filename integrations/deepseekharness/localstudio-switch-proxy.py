#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from localstudio_api import CONTROLLER_ROOT, controller_api_key, request_json

LISTEN_HOST = os.environ.get("DSH_LOCALSTUDIO_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("DSH_LOCALSTUDIO_PROXY_PORT", "1236"))
CONTROLLER = urllib.parse.urlsplit(CONTROLLER_ROOT)
UPSTREAM_HOST = CONTROLLER.hostname or "127.0.0.1"
UPSTREAM_PORT = CONTROLLER.port or 8080
INFERENCE_PATHS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
INFERENCE_LOCK = threading.Lock()


def ensure_model_loaded(model_id: str) -> tuple[str, bool]:
    payload = request_json("/recipes", timeout=10)
    if not isinstance(payload, list):
        raise RuntimeError("Local Studio returned an invalid recipe catalog")
    recipes = [recipe for recipe in payload if isinstance(recipe, dict)]
    target = next(
        (
            recipe
            for recipe in recipes
            if recipe.get("id") == model_id or recipe.get("served_model_name") == model_id
        ),
        None,
    )
    if target is None:
        raise RuntimeError(f"Local Studio recipe not found for {model_id}")
    recipe_id = target.get("id")
    if not isinstance(recipe_id, str) or not recipe_id:
        raise RuntimeError(f"Local Studio recipe id missing for {model_id}")
    if target.get("status") == "running":
        return recipe_id, False
    if any(recipe.get("status") == "running" for recipe in recipes):
        request_json("/evict", {}, timeout=120, method="POST")
    encoded = urllib.parse.quote(recipe_id, safe="")
    request_json(f"/launch/{encoded}", {}, timeout=1900, method="POST")
    return recipe_id, True


class LocalStudioProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DSH-LocalStudio-Switch/1.0"

    def _body(self) -> bytes:
        try:
            length = max(0, int(self.headers.get("Content-Length", "0")))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b""

    def _requested_model(self, path: str, body: bytes) -> str | None:
        if path not in INFERENCE_PATHS or not body:
            return None
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        model = payload.get("model") if isinstance(payload, dict) else None
        return model if isinstance(model, str) and model else None

    def _headers(self, body: bytes) -> dict[str, str]:
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS | {"host", "content-length", "authorization"}
        }
        headers["Authorization"] = f"Bearer {controller_api_key()}"
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        headers["Connection"] = "close"
        if body:
            headers["Content-Length"] = str(len(body))
        return headers

    def _send_error(self, status: int, message: str) -> None:
        payload = json.dumps(
            {"error": {"message": message, "type": "localstudio_switch_error"}}
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _forward(self) -> None:
        body = self._body()
        path = self.path.split("?", 1)[0]
        requested_model = self._requested_model(path, body)
        lock = INFERENCE_LOCK if requested_model else None
        if lock is not None:
            lock.acquire()
        try:
            if requested_model:
                try:
                    recipe_id, launched = ensure_model_loaded(requested_model)
                    action = "launched" if launched else "reused"
                    print(
                        f"localstudio-switch: {action} {recipe_id} for {requested_model}",
                        file=sys.stderr,
                        flush=True,
                    )
                except (
                    urllib.error.URLError,
                    TimeoutError,
                    OSError,
                    RuntimeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as error:
                    self._send_error(502, f"Local Studio model switch failed: {error}")
                    return
            upstream = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=3600)
            try:
                upstream.request(self.command, self.path, body=body or None, headers=self._headers(body))
                response = upstream.getresponse()
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() in HOP_BY_HOP_HEADERS | {"content-length", "server", "date"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (OSError, http.client.HTTPException) as error:
                if not self.wfile.closed:
                    self._send_error(502, f"Local Studio request failed: {error}")
            finally:
                upstream.close()
                self.close_connection = True
        finally:
            if lock is not None:
                lock.release()

    do_GET = _forward
    do_POST = _forward
    do_PUT = _forward
    do_PATCH = _forward
    do_DELETE = _forward
    do_OPTIONS = _forward

    def log_message(self, format_: str, *args: object) -> None:
        print(
            f"localstudio-switch: {self.address_string()} {format_ % args}",
            file=sys.stderr,
            flush=True,
        )


class LocalStudioProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    server = LocalStudioProxyServer((LISTEN_HOST, LISTEN_PORT), LocalStudioProxyHandler)
    print(
        f"localstudio-switch: listening on {LISTEN_HOST}:{LISTEN_PORT}, upstream {UPSTREAM_HOST}:{UPSTREAM_PORT}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

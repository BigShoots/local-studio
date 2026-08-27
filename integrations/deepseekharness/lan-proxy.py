#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import threading

LISTEN_HOST = os.environ.get("DSH_LAN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("DSH_LAN_PORT", "8082"))
UPSTREAM_HOST = os.environ.get("DSH_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("DSH_UPSTREAM_PORT", "3080"))
CONNECT_TIMEOUT_SECONDS = 10


def pipe(source: socket.socket, destination: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            destination.sendall(data)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle(client: socket.socket) -> None:
    try:
        upstream = socket.create_connection(
            (UPSTREAM_HOST, UPSTREAM_PORT), timeout=CONNECT_TIMEOUT_SECONDS
        )
        upstream.settimeout(None)
    except OSError:
        client.close()
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    pipe(upstream, client)
    client.close()
    upstream.close()


def main() -> int:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(128)
    print(
        f"lan-proxy: {LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}",
        flush=True,
    )
    while True:
        client, _address = server.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())

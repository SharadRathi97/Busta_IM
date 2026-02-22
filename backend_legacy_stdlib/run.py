from __future__ import annotations

import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from db import init_db
from web import application


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main() -> None:
    init_db()
    host = os.environ.get("BUSTA_HOST", "127.0.0.1")
    port = int(os.environ.get("BUSTA_PORT", "8000"))

    httpd = make_server(host, port, application, server_class=ThreadingWSGIServer)
    print(f"Busta IM running at http://{host}:{port}")
    print("Default admin credentials: admin / admin123")
    httpd.serve_forever()


if __name__ == "__main__":
    main()

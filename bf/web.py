"""Local web server for the BeatForge UI.

Serves the static app, streams state changes to the browser over SSE, and
accepts state writes and rendered audio back from it.
"""

import base64
import json
import mimetypes
import os
import re
import sys
import threading
import time

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # pragma: no cover - Python 3.6 fallback
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

from . import tools as tools_mod
from .state import migrate


def make_handler(project, res_root, data_root, quiet=True):
    web_dir = os.path.join(res_root, "web")
    export_dir = os.path.join(data_root, "exports")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "BeatForge"

        def log_message(self, fmt, *args):
            if not quiet:
                sys.stderr.write("[web] " + fmt % args + "\n")

        # -- helpers --------------------------------------------------------
        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj), "application/json; charset=utf-8")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            raw = self.rfile.read(n)
            return json.loads(raw.decode("utf-8"))

        def _query(self):
            if "?" not in self.path:
                return {}
            q = {}
            for part in self.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    q[k] = v
            return q

        # -- routes ---------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                self._json(project.snapshot())
                return
            if path == "/api/events":
                self._events(self._query().get("client"))
                return
            if path == "/api/meta":
                from .state import DRUM_ENGINES, MELODIC_ENGINES
                from . import theory
                self._json({
                    "drumEngines": DRUM_ENGINES,
                    "melodicEngines": MELODIC_ENGINES,
                    "scales": sorted(theory.SCALES),
                    "progressions": sorted(theory.PROGRESSIONS),
                    "genres": sorted(__import__("bf.generators", fromlist=["x"]).GENRES),
                })
                return
            if path == "/api/exports":
                files = sorted(os.listdir(export_dir)) if os.path.isdir(export_dir) else []
                self._json({"dir": export_dir, "files": [f for f in files if f.endswith(".wav")]})
                return
            self._static(path)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                body = self._body()
            except Exception as exc:
                self._json({"error": "bad json: %s" % exc}, 400)
                return

            if path == "/api/state":
                incoming = body.get("state")
                origin = body.get("client")
                if not isinstance(incoming, dict):
                    self._json({"error": "missing state"}, 400)
                    return
                clean = migrate(incoming)

                def apply(data):
                    keep_rev = data.get("rev", 0)
                    data.clear()
                    data.update(clean)
                    data["rev"] = keep_rev
                project.mutate(apply, origin=origin)
                self._json({"ok": True, "rev": project.snapshot()["rev"]})
                return

            if path in ("/api/undo", "/api/redo"):
                steps = int(body.get("steps") or 1)
                n = (project.undo if path == "/api/undo" else project.redo)(steps)
                undo_n, redo_n = project.history_depth()
                self._json({"ok": True, "applied": n, "undo": undo_n, "redo": redo_n,
                            "rev": project.snapshot()["rev"]})
                return

            if path == "/api/export":
                job = body.get("job")
                if body.get("error"):
                    tools_mod.complete_export(job, None, 0, body["error"])
                    self._json({"ok": True})
                    return
                name = re.sub(r"[^A-Za-z0-9 _.-]", "_", body.get("filename") or "beatforge")
                if not os.path.isdir(export_dir):
                    os.makedirs(export_dir)
                path_out = os.path.join(export_dir, "%s.wav" % name)
                n = 2
                while os.path.exists(path_out):
                    path_out = os.path.join(export_dir, "%s-%d.wav" % (name, n))
                    n += 1
                raw = base64.b64decode(body.get("wav", ""))
                with open(path_out, "wb") as fh:
                    fh.write(raw)
                tools_mod.complete_export(job, path_out, len(raw))
                self._json({"ok": True, "path": path_out, "bytes": len(raw)})
                return

            self._json({"error": "no such endpoint"}, 404)

        # -- SSE ------------------------------------------------------------
        def _events(self, client_id):
            entry = project.subscribe(client_id)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self._write_event("state", project.snapshot())
                while True:
                    entry["event"].wait(12)
                    entry["event"].clear()
                    while entry["queue"]:
                        name, payload = entry["queue"].pop(0)
                        self._write_event(name, payload)
                    if not entry["queue"]:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                project.unsubscribe(entry)

        def _write_event(self, name, payload):
            chunk = "event: %s\ndata: %s\n\n" % (name, json.dumps(payload, separators=(",", ":")))
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()

        # -- static ---------------------------------------------------------
        def _static(self, path):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.normpath(os.path.join(web_dir, rel))
            if not full.startswith(os.path.abspath(web_dir)) or not os.path.isfile(full):
                self._send(404, "not found")
                return
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            with open(full, "rb") as fh:
                self._send(200, fh.read(), ctype)

    return Handler


def start(project, res_root, data_root, host="127.0.0.1", port=8787, quiet=True):
    """Bind the web server on the first free port at or after `port`."""
    last = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(
                (host, candidate), make_handler(project, res_root, data_root, quiet))
            break
        except OSError as exc:
            last = exc
            httpd = None
    if httpd is None:
        raise RuntimeError("could not bind a port near %d: %s" % (port, last))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="beatforge-web")
    thread.daemon = True
    thread.start()
    return httpd, "http://%s:%d" % (host, httpd.server_address[1])

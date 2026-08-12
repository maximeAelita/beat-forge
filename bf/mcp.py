"""Minimal MCP server over stdio -- newline-delimited JSON-RPC 2.0.

No third-party dependencies: this machine only has the system Python, so the
protocol is implemented directly. stdout carries protocol traffic only; every
diagnostic goes to stderr.
"""

import json
import sys
import traceback

from . import tools as tools_mod

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "beatforge", "version": "1.0.0"}


def log(*parts):
    sys.stderr.write("[beatforge] " + " ".join(str(p) for p in parts) + "\n")
    sys.stderr.flush()


class MCPServer(object):
    def __init__(self, runner):
        self.runner = runner
        self.out = sys.stdout

    def send(self, msg):
        self.out.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.out.flush()

    def reply(self, rid, result):
        self.send({"jsonrpc": "2.0", "id": rid, "result": result})

    def error(self, rid, code, message):
        self.send({"jsonrpc": "2.0", "id": rid,
                   "error": {"code": code, "message": message}})

    def serve_forever(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.error(None, -32700, "parse error")
                continue
            try:
                self.handle(msg)
            except Exception as exc:  # never die on a single bad request
                log("handler crashed:", traceback.format_exc())
                if msg.get("id") is not None:
                    self.error(msg["id"], -32603, "internal error: %s" % exc)

    def handle(self, msg):
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            want = params.get("protocolVersion")
            version = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            self.reply(rid, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "BeatForge is a step-sequencer / beat maker. The browser UI at %s "
                    "shows and plays whatever these tools write, live. Workflow: "
                    "bf_get_project to see the grid, bf_generate_drums for a groove, "
                    "bf_generate_bass / bf_generate_melody / bf_generate_chords for the "
                    "musical parts, bf_set_steps and bf_edit_steps for hand edits, "
                    "bf_tracks for the mixer, bf_export_audio to render a wav."
                    % self.runner.url),
            })
            return

        if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
            return  # notifications get no response

        if method == "ping":
            self.reply(rid, {})
            return

        if method == "tools/list":
            self.reply(rid, {"tools": tools_mod.TOOLS})
            return

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                text = self.runner.call(name, args)
                if not isinstance(text, str):
                    text = json.dumps(text, indent=1)
                self.reply(rid, {"content": [{"type": "text", "text": text}],
                                 "isError": False})
            except Exception as exc:
                log("tool %s failed: %s" % (name, traceback.format_exc()))
                self.reply(rid, {
                    "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}],
                    "isError": True})
            return

        if method == "resources/list":
            self.reply(rid, {"resources": []})
            return
        if method == "prompts/list":
            self.reply(rid, {"prompts": []})
            return

        if rid is not None:
            self.error(rid, -32601, "method not found: %s" % method)

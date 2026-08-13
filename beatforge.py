#!/usr/bin/env python3
"""BeatForge -- beat maker + built-in MCP server.

    python3 beatforge.py            # web UI only, opens in your browser
    python3 beatforge.py --mcp      # MCP server on stdio + web UI (how Claude runs it)
    python3 beatforge.py --port 9000

Both modes share one project state, so anything Claude writes shows up live in
the browser and anything you click in the browser is visible to Claude.
"""

import argparse
import os
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bf import mcp as mcp_mod  # noqa: E402
from bf import paths  # noqa: E402
from bf import tools as tools_mod  # noqa: E402
from bf import web as web_mod  # noqa: E402
from bf.state import Project  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="BeatForge beat maker")
    ap.add_argument("--mcp", action="store_true",
                    help="run as an MCP server on stdio (also serves the web UI)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-web", action="store_true", help="MCP only, no web UI")
    ap.add_argument("--open", action="store_true", help="open the UI in a browser")
    ap.add_argument("--no-open", action="store_true",
                    help="never open a browser (the packaged app otherwise does)")
    ap.add_argument("--verbose", action="store_true", help="log HTTP requests to stderr")
    args = ap.parse_args()

    res_root = paths.resource_root()
    data_root = paths.ensure_dirs(paths.data_root())

    autosave = os.path.join(data_root, "projects", "_autosave.json")
    project = Project(autosave_path=autosave)

    url = "(web UI disabled)"
    httpd = None
    if not args.no_web:
        httpd, url = web_mod.start(project, res_root, data_root, args.host, args.port,
                                   quiet=not args.verbose)

    runner = tools_mod.ToolRunner(project, data_root, url)

    if args.mcp:
        mcp_mod.log("MCP server ready; UI at", url)
        server = mcp_mod.MCPServer(runner)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    print("BeatForge running at %s" % url)
    print("Project autosaves to %s" % autosave)
    print("Ctrl-C to stop.")
    # Double-clicking the packaged app should just open the studio.
    if (args.open or paths.is_frozen()) and not args.no_open:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        if httpd:
            httpd.shutdown()


if __name__ == "__main__":
    main()

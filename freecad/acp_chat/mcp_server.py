"""Tiny in-process MCP server (streamable HTTP, JSON responses only) exposing
one tool, `execute_python`, to the ACP agent.

`handle()` is pure so it can be tested without FreeCAD; `serve()` runs it on a
loopback thread with a bearer token, and the caller supplies `run_python`
(which must hop to the GUI thread).
"""

import json
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

Json = dict[str, Any]
# code -> (output, ok)
RunPython = Callable[[str], tuple[str, bool]]

DEFAULT_PROTOCOL = "2025-06-18"
TOOLS: list[Json] = [
    {
        "name": "execute_python",
        "description": (
            "Run Python in the user's live FreeCAD session (GUI thread). "
            "FreeCAD/App, FreeCADGui/Gui are preimported; variables persist between calls. "
            "Returns stdout, stderr and any traceback. Call doc.recompute() after edits."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source"}},
            "required": ["code"],
        },
    }
]


def handle(msg: Json, run_python: RunPython) -> Json | None:
    """Return the JSON-RPC response for `msg`, or None for notifications."""
    method, req_id = msg.get("method"), msg.get("id")
    if req_id is None:
        return None
    params = msg.get("params") or {}
    result: Json
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "freecad", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call" and params.get("name") == "execute_python":
        output, ok = run_python((params.get("arguments") or {}).get("code", ""))
        result = {"content": [{"type": "text", "text": output or "(no output)"}], "isError": not ok}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown: {method}"}}
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def serve(run_python: RunPython) -> tuple[str, str]:
    """Start on 127.0.0.1 with a random port; return (url, token)."""
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, code: int, body: Json | None = None) -> None:
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # no server-initiated SSE stream
            self._reply(405)

        def do_POST(self) -> None:
            if not secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                return self._reply(401)
            try:
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except ValueError:
                return self._reply(400)
            resp = handle(msg, run_python)
            self._reply(202) if resp is None else self._reply(200, resp)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/mcp", token

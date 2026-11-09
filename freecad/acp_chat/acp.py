"""Minimal Agent Client Protocol (ACP) client: JSON-RPC 2.0, one message per line.

Transport-agnostic and Qt-free so it can be tested without FreeCAD. The caller
feeds agent stdout bytes into `feed()` and supplies `write(bytes)` for stdin.
Spec: https://agentclientprotocol.com
"""

import json

PROTOCOL_VERSION = 1
AUTH_REQUIRED = -32000


class AcpClient:
    def __init__(self, write, on_update, on_permission, on_error):
        self._write = write
        self._on_update = on_update  # (update_dict)
        self._on_permission = on_permission  # (params, reply(option_id_or_None))
        self._on_error = on_error  # (message_str)
        self._buf = b""
        self._next_id = 0
        self._pending = {}
        self.session_id = None
        self.agent_capabilities = {}
        self.agent_info = {}
        self.auth_methods = []
        self.config_options = []  # ACP session config options (mode, model, effort, ...)

    # -- outgoing ---------------------------------------------------------

    def _send(self, msg):
        msg["jsonrpc"] = "2.0"
        self._write((json.dumps(msg) + "\n").encode("utf-8"))

    def request(self, method, params, on_result=None, on_error=None):
        self._next_id += 1
        self._pending[self._next_id] = (method, on_result, on_error)
        self._send({"id": self._next_id, "method": method, "params": params})

    def notify(self, method, params):
        self._send({"method": method, "params": params})

    def start(self, cwd, mcp_servers, on_ready, on_auth_required=None):
        """initialize, then session/new; calls on_ready(session_id). If the agent
        needs sign-in, calls on_auth_required(methods, authenticate(method_id))."""

        def new_session():
            self.request("session/new", {"cwd": cwd, "mcpServers": mcp_servers}, got_session, session_failed)

        def session_failed(err):
            if err.get("code") == AUTH_REQUIRED and self.auth_methods and on_auth_required:
                on_auth_required(
                    self.auth_methods,
                    lambda method_id: self.request("authenticate", {"methodId": method_id}, lambda _r: new_session()),
                )
            else:
                self._on_error(f"session/new failed: {err.get('message')} {err.get('data') or ''}")

        def got_session(result):
            self.session_id = result["sessionId"]
            self.config_options = result.get("configOptions") or []
            on_ready(self.session_id)

        def initialized(result):
            self.agent_capabilities = result.get("agentCapabilities") or {}
            self.agent_info = result.get("agentInfo") or {}
            self.auth_methods = result.get("authMethods") or []
            new_session()

        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                # No fs/terminal: the agent edits FreeCAD through MCP tools.
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "freecad-acp-chat", "version": "0.1.0"},
            },
            initialized,
        )

    def supports_images(self):
        return bool((self.agent_capabilities.get("promptCapabilities") or {}).get("image"))

    def prompt(self, blocks, on_done):
        """Send a user turn (list of ACP content blocks); on_done(stop_reason) when the agent finishes."""
        self.request(
            "session/prompt",
            {"sessionId": self.session_id, "prompt": blocks},
            lambda result: on_done(result.get("stopReason")),
        )

    def set_config(self, config_id, value):
        """Change one config option; the agent replies with the full updated list."""
        self.request(
            "session/set_config_option",
            {"sessionId": self.session_id, "configId": config_id, "value": value},
            lambda result: self._config_changed(result.get("configOptions")),
        )

    def _config_changed(self, options):
        if options is not None:
            self.config_options = options
            self._on_update({"sessionUpdate": "config_option_update", "configOptions": options})

    def cancel(self):
        if self.session_id:
            self.notify("session/cancel", {"sessionId": self.session_id})

    # -- incoming ---------------------------------------------------------

    def feed(self, data):
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._on_error(f"Bad JSON from agent: {line[:200]!r}")
                continue
            self._dispatch(msg)

    def _dispatch(self, msg):
        method = msg.get("method")
        if method is None:  # response to one of our requests
            name, on_result, on_error = self._pending.pop(msg.get("id"), (None, None, None))
            if "error" in msg:
                err = msg["error"]
                if on_error:
                    on_error(err)
                else:
                    self._on_error(f"{name} failed: {err.get('message')} {err.get('data') or ''}")
            elif on_result:
                on_result(msg.get("result") or {})
        elif method == "session/update":
            update = msg["params"]["update"]
            kind = update.get("sessionUpdate")
            if kind == "config_option_update":
                self.config_options = update.get("configOptions") or self.config_options
            elif kind == "current_mode_update":  # legacy modes API; mirror it into the mode option
                for opt in self.config_options:
                    if opt.get("category") == "mode":
                        opt["currentValue"] = update.get("currentModeId")
                return self._on_update({"sessionUpdate": "config_option_update", "configOptions": self.config_options})
            self._on_update(update)
        elif method == "session/request_permission" and "id" in msg:
            req_id = msg["id"]

            def reply(option_id):
                outcome = (
                    {"outcome": "selected", "optionId": option_id}
                    if option_id
                    else {"outcome": "cancelled"}
                )
                self._send({"id": req_id, "result": {"outcome": outcome}})

            self._on_permission(msg["params"], reply)
        elif "id" in msg:
            self._send(
                {"id": msg["id"], "error": {"code": -32601, "message": f"Method not found: {method}"}}
            )

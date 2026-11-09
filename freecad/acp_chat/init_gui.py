"""AI chat dock for FreeCAD. Runs an ACP agent as a subprocess and gives it a
built-in MCP server (mcp_server.py) so it can drive the live session.

Settings (Tools > Edit parameters > BaseApp/Preferences/Mod/ACPChat):
  AgentCommand  shell command that starts an ACP agent
"""

import contextlib
import io
import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from freecad.acp_chat import context, mcp_server, ui
from freecad.acp_chat.acp import AcpClient

PREFS = App.ParamGet("User parameter:BaseApp/Preferences/Mod/ACPChat")
DEFAULT_AGENT = "npx -y @agentclientprotocol/claude-agent-acp"
FIRST_TURN_HINT = (
    "You are an assistant inside FreeCAD. Use the `freecad` MCP tools to inspect "
    "and modify the user's open session.\n\n"
)


class _GuiRunner(QtCore.QObject):
    """Runs agent Python on the GUI thread; FreeCAD is not thread-safe."""

    _call = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.namespace = {"FreeCAD": App, "App": App, "FreeCADGui": Gui, "Gui": Gui}
        self._call.connect(self._run, QtCore.Qt.BlockingQueuedConnection)

    @QtCore.Slot(object)
    def _run(self, job):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(job["code"], self.namespace)
            job["result"] = (out.getvalue(), True)
        except BaseException:
            job["result"] = (out.getvalue() + traceback.format_exc(), False)

    def __call__(self, code):  # called from the HTTP server thread
        job = {"code": code}
        self._call.emit(job)
        return job["result"]


_server = None


def _mcp_servers():
    """Start the MCP server once per FreeCAD session; return the ACP mcpServers list."""
    global _server
    if _server is None:
        runner = _GuiRunner()
        _server = (runner, *mcp_server.serve(runner))
    _runner, url, token = _server
    return [{"type": "http", "name": "freecad", "url": url,
             "headers": [{"name": "Authorization", "value": f"Bearer {token}"}]}]


def _session_cwd():
    doc = App.ActiveDocument
    if doc and doc.FileName:
        return os.path.dirname(doc.FileName)
    return os.path.expanduser("~")


class ChatDock(QtWidgets.QDockWidget):
    def __init__(self, parent):
        super().__init__("AI Chat", parent)
        self.setObjectName("ACPChatDock")
        self.proc = None
        self.client = None
        self.first_turn = True

        root = QtWidgets.QWidget()
        root.setObjectName("chatRoot")
        self.t = ui.theme(parent)

        header = QtWidgets.QWidget()
        header.setObjectName("header")
        head = QtWidgets.QHBoxLayout(header)
        head.setContentsMargins(12, 6, 6, 6)
        title = QtWidgets.QLabel("AI Chat")
        title.setObjectName("title")
        self.agent_label = ui._label(muted=True)
        new_btn = QtWidgets.QToolButton()
        new_btn.setObjectName("iconBtn")
        new_btn.setIcon(ui.icon("plus", self.t["muted"]))
        new_btn.setToolTip("New chat (restarts the agent)")
        new_btn.setAccessibleName("New chat")
        new_btn.clicked.connect(self.restart)
        head.addWidget(title)
        head.addWidget(self.agent_label, 1)
        head.addWidget(new_btn)

        self.transcript = ui.Transcript()
        self.working = ui._label("Working", muted=True)
        self.working.hide()
        self._dots = QtCore.QTimer(self)
        self._dots.timeout.connect(
            lambda: self.working.setText("Working" + "." * ((self.working.text().count(".") + 1) % 4))
        )
        self.composer = ui.Composer(self.t)
        self.composer.send.connect(self.send)
        self.composer.stop.connect(lambda: self.client and self.client.cancel())
        self.composer.sel_btn.setChecked(PREFS.GetBool("IncludeSelection", True))
        self.composer.view_btn.setChecked(PREFS.GetBool("IncludeView", False))
        self.composer.toggles_changed.connect(self._save_toggles)
        self.composer.config_changed.connect(self._set_config)
        self.composer.unselect.connect(lambda key: Gui.Selection.removeSelection(*key))
        # Coalesce bursts of selection events (box select) into one refresh.
        self._sel_timer = QtCore.QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.timeout.connect(lambda: self.composer.set_selection(context.selection_items()))
        self._sel_observer = _SelectionObserver(self._sel_timer.start)
        Gui.Selection.addObserver(self._sel_observer)
        self._sel_timer.start()

        bottom = QtWidgets.QVBoxLayout()
        bottom.setContentsMargins(10, 4, 10, 10)
        bottom.setSpacing(6)
        bottom.addWidget(self.working)
        bottom.addWidget(self.composer)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(bottom)
        root.setStyleSheet(ui.stylesheet(self.t))
        self.setWidget(root)
        self.visibilityChanged.connect(self._start_once)

    # -- process lifecycle -------------------------------------------------

    def _start_once(self, visible):
        if visible and self.proc is None:
            self.restart()

    def _save_toggles(self):
        PREFS.SetBool("IncludeSelection", self.composer.sel_btn.isChecked())
        PREFS.SetBool("IncludeView", self.composer.view_btn.isChecked())

    def restart(self):
        if self.proc:
            self.proc.finished.disconnect()
            self.proc.kill()
            self.proc.waitForFinished(2000)
        self.transcript.clear()
        self.composer.set_config_options([])
        self.first_turn = True
        self._set_state(ready=False, busy=False)
        cmd = PREFS.GetString("AgentCommand", DEFAULT_AGENT)
        self.agent_label.setText("starting…")
        self.agent_label.setToolTip(cmd)
        self.proc = QtCore.QProcess(self)
        if sys.platform == "win32":
            self.proc.setProgram("cmd")
            self.proc.setArguments(["/c", cmd])
        else:
            # Login shell so GUI-launched FreeCAD finds npx/node on the user's PATH.
            self.proc.setProgram(os.environ.get("SHELL", "/bin/sh"))
            self.proc.setArguments(["-lc", cmd])
        self.proc.readyReadStandardOutput.connect(
            lambda: self.client.feed(bytes(self.proc.readAllStandardOutput()))
        )
        self.proc.readyReadStandardError.connect(
            lambda: App.Console.PrintLog(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace"))
        )
        self.proc.finished.connect(self._on_exit)
        self.client = AcpClient(
            lambda data: self.proc.write(data),
            self._on_update,
            self.transcript.permission,
            self._on_error,
        )
        self.proc.start()
        self.client.start(_session_cwd(), _mcp_servers(), self._on_ready)

    def _on_ready(self, _session_id):
        info = self.client.agent_info
        self.agent_label.setText(info.get("title") or info.get("name") or "")
        self._set_state(ready=True, busy=False)
        # Re-apply the user's last choices (model, effort, mode, ...) to the new session.
        for opt in self.client.config_options:
            saved = PREFS.GetString(f"Config_{opt['id']}", "")
            values = {c["value"] for c in opt.get("options", [])}
            if saved in values and saved != opt.get("currentValue"):
                self.client.set_config(opt["id"], saved)
        self.composer.set_config_options(self.client.config_options)

    def _set_config(self, config_id, value):
        PREFS.SetString(f"Config_{config_id}", value)
        self.client.set_config(config_id, value)

    def _on_exit(self, code, _status):
        self.agent_label.setText("stopped")
        self.transcript.notice(
            f"The agent stopped (exit code {code}). Its log is in the Report view. Start a new chat to try again.",
            error=True,
        )
        self._set_state(ready=False, busy=False)

    # -- chat ----------------------------------------------------------------

    def send(self, text):
        attachments = self._capture()
        self.transcript.user(text, attachments)
        self._set_state(ready=True, busy=True)
        if self.first_turn:
            text, self.first_turn = FIRST_TURN_HINT + text, False
        blocks = [{"type": "text", "text": text}] + [block for _label, block in attachments]
        self.client.prompt(blocks, self._on_done)

    def _capture(self):
        """Selection and view as they are now, for the toggles that are on."""
        out = []
        if self.composer.sel_btn.isChecked():
            text = context.selection_text()
            if text:
                out.append((f"{text.count(chr(10) + '- ')} selected", {"type": "text", "text": text}))
        if self.composer.view_btn.isChecked():
            if not self.client.supports_images():
                self.transcript.notice("This agent does not accept images, so the view was not sent.")
            else:
                data = context.view_image()
                if data is None:
                    self.transcript.notice("No 3D view is open, so the view was not sent.")
                else:
                    out.append(("3D view", {"type": "image", "mimeType": "image/png", "data": data}))
        return out

    def _on_done(self, stop_reason):
        if stop_reason == "cancelled":
            self.transcript.notice("Stopped.")
        elif stop_reason not in ("end_turn", None):
            self.transcript.notice(f"The agent ended its turn early ({stop_reason}).")
        self._set_state(ready=True, busy=False)

    def _on_update(self, update):
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk" and update["content"].get("type") == "text":
            self.transcript.assistant_chunk(update["content"]["text"])
        elif kind in ("tool_call", "tool_call_update"):
            self.transcript.tool(update)
        elif kind == "config_option_update":
            self.composer.set_config_options(self.client.config_options)

    def _on_error(self, message):
        self.transcript.notice(message, error=True)
        App.Console.PrintError(f"ACP Chat: {message}\n")
        self._set_state(ready=bool(self.client and self.client.session_id), busy=False)

    def _set_state(self, ready, busy):
        self.composer.set_state(ready, busy)
        self.working.setVisible(busy)
        self._dots.start(450) if busy else self._dots.stop()

    def closeEvent(self, event):
        if self.proc:
            self.proc.kill()
        super().closeEvent(event)


class _SelectionObserver:
    def __init__(self, changed):
        self.changed = changed

    def addSelection(self, *_args):
        self.changed()

    def removeSelection(self, *_args):
        self.changed()

    def setSelection(self, *_args):
        self.changed()

    def clearSelection(self, *_args):
        self.changed()


def _dock():
    mw = Gui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, "ACPChatDock")
    if dock is None:
        dock = ChatDock(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    return dock


class ToggleChatCommand:
    def GetResources(self):
        return {
            "Pixmap": os.path.join(os.path.dirname(__file__), "Resources", "icons", "acp_chat.svg"),
            "MenuText": "AI Chat",
            "ToolTip": "Show or hide the AI chat panel",
        }

    def Activated(self):
        dock = _dock()
        dock.setVisible(not dock.isVisible())  # agent starts on first show


class _ToolbarManipulator:
    def modifyToolBars(self):
        # First entry creates the toolbar under the root, second adds the command.
        return [{"append": "AI Chat", "toolBar": ""}, {"append": "ACPChat_Toggle", "toolBar": "AI Chat"}]


Gui.addCommand("ACPChat_Toggle", ToggleChatCommand())
Gui.addWorkbenchManipulator(_ToolbarManipulator())

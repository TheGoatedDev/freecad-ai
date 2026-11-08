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
from PySide import QtCore, QtGui, QtWidgets

from freecad.acp_chat import context, mcp_server
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
        self.attachments = []  # (label, ACP content block) sent with the next message

        self.log = QtWidgets.QTextBrowser()
        self.input = QtWidgets.QPlainTextEdit()
        self.input.setPlaceholderText("Ask the agent… (Ctrl+Enter to send)")
        self.input.setMaximumHeight(90)
        self.send_btn = QtWidgets.QPushButton("Send")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        new_btn = QtWidgets.QPushButton("New session")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn.clicked.connect(lambda: self.client and self.client.cancel())
        new_btn.clicked.connect(self.restart)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.input, self.send)
        sel_btn = QtWidgets.QPushButton("Attach selection")
        sel_btn.setToolTip("Send the selected faces, edges, vertices and objects with the next message")
        view_btn = QtWidgets.QPushButton("Attach view")
        view_btn.setToolTip("Send a picture of the current 3D view with the next message")
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.attached_label = QtWidgets.QLabel()
        self.attached_label.setWordWrap(True)
        sel_btn.clicked.connect(self.attach_selection)
        view_btn.clicked.connect(self.attach_view)
        self.clear_btn.clicked.connect(lambda: self._set_attachments([]))
        attach_row = QtWidgets.QHBoxLayout()
        for w in (sel_btn, view_btn, self.clear_btn):
            attach_row.addWidget(w)
        attach_row.addStretch()

        buttons = QtWidgets.QHBoxLayout()
        for b in (self.send_btn, self.stop_btn, new_btn):
            buttons.addWidget(b)
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.log)
        layout.addLayout(attach_row)
        layout.addWidget(self.attached_label)
        layout.addWidget(self.input)
        layout.addLayout(buttons)
        body = QtWidgets.QWidget()
        body.setLayout(layout)
        self.setWidget(body)
        self._set_busy(True)
        self._set_attachments([])
        self.visibilityChanged.connect(self._start_once)

    # -- process lifecycle -------------------------------------------------

    def _start_once(self, visible):
        if visible and self.proc is None:
            self.restart()

    def restart(self):
        if self.proc:
            self.proc.kill()
            self.proc.waitForFinished(2000)
        self.log.clear()
        self.first_turn = True
        self._set_attachments([])
        self._set_busy(True)
        cmd = PREFS.GetString("AgentCommand", DEFAULT_AGENT)
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
            self._on_permission,
            self._on_error,
        )
        self._append(f"Starting agent: {cmd}\n")
        self.proc.start()
        self.client.start(_session_cwd(), _mcp_servers(), lambda _sid: (self._append("Ready.\n"), self._set_busy(False)))

    def _on_exit(self, code, _status):
        self._append(f"\n⚠ Agent exited (code {code}). Check Report view for its log; click New session to retry.\n")
        self._set_busy(True)

    # -- chat ----------------------------------------------------------------

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or not self.send_btn.isEnabled():
            return
        self.input.clear()
        self._append(f"\n\n▶ {text}\n\n")
        self._set_busy(True)
        for label, _block in self.attachments:
            self._append(f"📎 {label}\n")
        if self.first_turn:
            text, self.first_turn = FIRST_TURN_HINT + text, False
        blocks = [{"type": "text", "text": text}] + [block for _label, block in self.attachments]
        self._set_attachments([])
        self.client.prompt(blocks, self._on_done)

    def attach_selection(self):
        text = context.selection_text()
        if text is None:
            return self._append("\n⚠ Nothing selected.\n")
        count = text.count("\n- ")
        self._set_attachments(self.attachments + [(f"selection ({count})", {"type": "text", "text": text})])

    def attach_view(self):
        if self.client and self.client.session_id and not self.client.supports_images():
            return self._append("\n⚠ This agent does not accept images.\n")
        data = context.view_image()
        if data is None:
            return self._append("\n⚠ No 3D view open.\n")
        self._set_attachments(self.attachments + [("view", {"type": "image", "mimeType": "image/png", "data": data})])

    def _set_attachments(self, attachments):
        self.attachments = attachments
        labels = ", ".join(label for label, _block in attachments)
        self.attached_label.setText(f"📎 {labels}" if labels else "")
        self.attached_label.setVisible(bool(labels))
        self.clear_btn.setVisible(bool(labels))

    def _on_done(self, stop_reason):
        if stop_reason not in ("end_turn", None):
            self._append(f"\n[{stop_reason}]")
        self._set_busy(False)

    def _on_update(self, update):
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk" and update["content"].get("type") == "text":
            self._append(update["content"]["text"])
        elif kind == "tool_call":
            self._append(f"\n🔧 {update.get('title', 'tool')}\n")
        elif kind == "tool_call_update" and update.get("status") == "failed":
            self._append(f"\n✗ tool failed: {update.get('title') or update.get('toolCallId')}\n")

    def _on_permission(self, params, reply):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Agent requests permission")
        box.setText((params.get("toolCall") or {}).get("title") or "Allow this action?")
        buttons = {
            box.addButton(opt["name"], QtWidgets.QMessageBox.ActionRole): opt["optionId"]
            for opt in params.get("options", [])
        }
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        reply(buttons.get(box.clickedButton()))

    def _on_error(self, message):
        self._append(f"\n⚠ {message}\n")
        App.Console.PrintError(f"ACP Chat: {message}\n")
        self._set_busy(False)

    # -- ui helpers -----------------------------------------------------------

    def _append(self, text):
        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.ensureCursorVisible()

    def _set_busy(self, busy):
        self.send_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)

    def closeEvent(self, event):
        if self.proc:
            self.proc.kill()
        super().closeEvent(event)


def _dock():
    mw = Gui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, "ACPChatDock")
    if dock is None:
        dock = ChatDock(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    return dock


class ToggleChatCommand:
    def GetResources(self):
        return {"MenuText": "AI Chat", "ToolTip": "Show or hide the AI chat panel"}

    def Activated(self):
        dock = _dock()
        dock.setVisible(not dock.isVisible())  # agent starts on first show


class _ToolbarManipulator:
    def modifyToolBars(self):
        # First entry creates the toolbar under the root, second adds the command.
        return [{"append": "AI Chat", "toolBar": ""}, {"append": "ACPChat_Toggle", "toolBar": "AI Chat"}]


Gui.addCommand("ACPChat_Toggle", ToggleChatCommand())
Gui.addWorkbenchManipulator(_ToolbarManipulator())

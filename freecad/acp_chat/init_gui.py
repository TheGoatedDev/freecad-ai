"""AI chat dock for FreeCAD. Runs an ACP agent as a subprocess and gives it a
built-in MCP server (mcp_server.py) so it can drive the live session.

Settings (Tools > Edit parameters > BaseApp/Preferences/Mod/ACPChat):
  AgentCommand   shell command that starts an ACP agent
  WorkingFolder  folder the agent starts in (Edit > Preferences > AI Chat)
"""

import contextlib
import io
import os
import sys
import traceback
from collections.abc import Callable
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from freecad.acp_chat import context, mcp_server, ui
from freecad.acp_chat.acp import AcpClient, Json

PREFS = App.ParamGet("User parameter:BaseApp/Preferences/Mod/ACPChat")
# ACP agents offered in the provider menu: (name, shell command).
PROVIDERS = [
    ("Claude", "npx -y @agentclientprotocol/claude-agent-acp"),
    ("Codex", "npx -y @agentclientprotocol/codex-acp"),
    ("Gemini", "npx -y @google/gemini-cli --acp"),
    ("opencode", "npx -y opencode-ai acp"),
    ("GitHub Copilot", "npx -y @github/copilot --acp"),
]
DEFAULT_AGENT = PROVIDERS[0][1]
ICON = os.path.join(os.path.dirname(__file__), "Resources", "icons", "acp_chat.svg")
FIRST_TURN_HINT = (
    "You are an assistant inside FreeCAD. Use the `freecad` MCP tools to inspect "
    "and modify the user's open session.\n\n"
)


class _GuiRunner(QtCore.QObject):
    """Runs agent Python on the GUI thread; FreeCAD is not thread-safe."""

    _call = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.namespace: dict[str, Any] = {"FreeCAD": App, "App": App, "FreeCADGui": Gui, "Gui": Gui}
        self._call.connect(self._run, QtCore.Qt.BlockingQueuedConnection)

    @QtCore.Slot(object)
    def _run(self, job: dict[str, Any]) -> None:
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(job["code"], self.namespace)
            job["result"] = (out.getvalue(), True)
        except BaseException:
            job["result"] = (out.getvalue() + traceback.format_exc(), False)

    def __call__(self, code: str) -> tuple[str, bool]:  # called from the HTTP server thread
        job: dict[str, Any] = {"code": code}
        self._call.emit(job)
        result: tuple[str, bool] = job["result"]
        return result


_server: tuple[_GuiRunner, str, str] | None = None


def _mcp_servers() -> list[Json]:
    """Start the MCP server once per FreeCAD session; return the ACP mcpServers list."""
    global _server
    if _server is None:
        runner = _GuiRunner()
        _server = (runner, *mcp_server.serve(runner))
    _runner, url, token = _server
    return [
        {
            "type": "http",
            "name": "freecad",
            "url": url,
            "headers": [{"name": "Authorization", "value": f"Bearer {token}"}],
        }
    ]


def _working_folder() -> str:
    return os.path.expanduser(str(PREFS.GetString("WorkingFolder", "")).strip())


def _session_cwd() -> str:
    """The working folder if set, so the agent reads the CLAUDE.md/AGENTS.md kept there."""
    folder = _working_folder()
    if folder and os.path.isdir(folder):
        return folder
    doc = App.ActiveDocument
    if doc and doc.FileName:
        return str(os.path.dirname(doc.FileName))
    return os.path.expanduser("~")


class ChatDock(QtWidgets.QDockWidget):
    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__("AI Chat", parent)
        self.setObjectName("ACPChatDock")
        self.proc: QtCore.QProcess | None = None
        self.client: AcpClient | None = None
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
        self.provider_btn = ui.pill("Provider")
        self.provider_btn.setLayoutDirection(QtCore.Qt.RightToLeft)  # chevron after the text
        self.provider_btn.menu().setLayoutDirection(QtCore.Qt.LeftToRight)
        self.provider_btn.setIcon(ui.icon("chevron_down", self.t["muted"], 12))
        self.provider_btn.menu().aboutToShow.connect(self._fill_provider_menu)
        new_btn = QtWidgets.QToolButton()
        new_btn.setObjectName("iconBtn")
        new_btn.setIcon(ui.icon("plus", self.t["muted"]))
        new_btn.setToolTip("New chat (restarts the agent)")
        new_btn.setAccessibleName("New chat")
        new_btn.clicked.connect(self.restart)
        settings_btn = QtWidgets.QToolButton()
        settings_btn.setObjectName("iconBtn")
        settings_btn.setIcon(ui.icon("sliders", self.t["muted"]))
        settings_btn.setToolTip("Settings")
        settings_btn.setAccessibleName("Settings")
        settings_btn.clicked.connect(lambda: Gui.showPreferences("AI Chat", 0))
        head.addWidget(title)
        head.addWidget(self.provider_btn)
        head.addStretch()
        head.addWidget(settings_btn)
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

    def _start_once(self, visible: bool) -> None:
        if visible and self.proc is None:
            self.restart()

    def _save_toggles(self) -> None:
        PREFS.SetBool("IncludeSelection", self.composer.sel_btn.isChecked())
        PREFS.SetBool("IncludeView", self.composer.view_btn.isChecked())

    def restart(self) -> None:
        if self.proc:
            self.proc.finished.disconnect()
            self.proc.kill()
            self.proc.waitForFinished(2000)
        self.transcript.clear()
        self.composer.set_config_options([])
        self.first_turn = True
        self._set_state(ready=False, busy=False)
        cmd = PREFS.GetString("AgentCommand", DEFAULT_AGENT)
        name = next((n for n, c in PROVIDERS if c == cmd), "Custom")
        self.provider_btn.setText(name)
        self.provider_btn.setToolTip(f"Provider: {name}\n{cmd}")
        proc = self.proc = QtCore.QProcess(self)
        if sys.platform == "win32":
            proc.setProgram("cmd")
            proc.setArguments(["/c", cmd])
        else:
            # Login shell so GUI-launched FreeCAD finds npx/node on the user's PATH.
            proc.setProgram(os.environ.get("SHELL", "/bin/sh"))
            proc.setArguments(["-lc", cmd])
        client = self.client = AcpClient(
            lambda data: proc.write(data),
            self._on_update,
            self.transcript.permission,
            self._on_error,
        )
        proc.readyReadStandardOutput.connect(lambda: client.feed(bytes(proc.readAllStandardOutput())))
        proc.readyReadStandardError.connect(
            lambda: App.Console.PrintLog(bytes(proc.readAllStandardError()).decode("utf-8", "replace"))
        )
        proc.finished.connect(self._on_exit)
        cwd = _session_cwd()
        folder = _working_folder()
        if folder and folder != cwd:
            self.transcript.notice(f"The working folder {folder} does not exist, so the agent started in {cwd}.")
        proc.setWorkingDirectory(cwd)
        proc.start()
        self.client.start(
            cwd,
            _mcp_servers(),
            self._on_ready,
            self._sign_in,
        )

    def _sign_in(self, methods: list[Json], authenticate: Callable[[str], None]) -> None:
        self.transcript.sign_in(self.provider_btn.text(), methods, authenticate)
        self.composer.edit.setPlaceholderText("Sign in above to start")

    def _on_ready(self, _session_id: str) -> None:
        assert self.client is not None
        self._set_state(ready=True, busy=False)
        # Re-apply the user's last choices (model, effort, mode, ...) to the new session.
        for opt in self.client.config_options:
            saved = PREFS.GetString(f"Config_{opt['id']}", "")
            values = {c["value"] for c in opt.get("options", [])}
            if saved in values and saved != opt.get("currentValue"):
                self.client.set_config(opt["id"], saved)
        self.composer.set_config_options(self.client.config_options)

    def _fill_provider_menu(self) -> None:
        menu = self.provider_btn.menu()
        menu.clear()
        current = PREFS.GetString("AgentCommand", DEFAULT_AGENT)
        group = QtGui.QActionGroup(menu)
        for name, cmd in PROVIDERS:
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(cmd == current)
            act.setToolTip(cmd)
            act.triggered.connect(lambda _=False, c=cmd: self._switch_provider(c))
            group.addAction(act)
        menu.addSeparator()
        custom = menu.addAction("Custom command…")
        custom.setCheckable(True)
        custom.setChecked(current not in {c for _n, c in PROVIDERS})
        custom.triggered.connect(self._custom_provider)
        group.addAction(custom)

    def _custom_provider(self) -> None:
        current = PREFS.GetString("AgentCommand", DEFAULT_AGENT)
        cmd, ok = QtWidgets.QInputDialog.getText(
            self, "Custom ACP agent", "Shell command that starts an ACP agent on stdio:", text=current
        )
        if ok and cmd.strip():
            self._switch_provider(cmd.strip())

    def _switch_provider(self, cmd: str) -> None:
        """Switching provider starts a new chat with the new agent."""
        if cmd != PREFS.GetString("AgentCommand", DEFAULT_AGENT):
            PREFS.SetString("AgentCommand", cmd)
            self.restart()

    def _set_config(self, config_id: str, value: str) -> None:
        assert self.client is not None
        PREFS.SetString(f"Config_{config_id}", value)
        self.client.set_config(config_id, value)

    def _on_exit(self, code: int, _status: Any) -> None:
        self.transcript.notice(
            f"The agent stopped (exit code {code}). Its log is in the Report view. Start a new chat to try again.",
            error=True,
        )
        self._set_state(ready=False, busy=False)

    # -- chat ----------------------------------------------------------------

    def send(self, text: str) -> None:
        assert self.client is not None
        attachments = self._capture()
        self.transcript.user(text, attachments)
        self._set_state(ready=True, busy=True)
        if self.first_turn:
            text, self.first_turn = FIRST_TURN_HINT + text, False
        blocks = [{"type": "text", "text": text}] + [block for _label, block in attachments]
        self.client.prompt(blocks, self._on_done)

    def _capture(self) -> list[tuple[str, Json]]:
        """Selection and view as they are now, for the toggles that are on."""
        assert self.client is not None
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

    def _on_done(self, stop_reason: str | None) -> None:
        if stop_reason == "cancelled":
            self.transcript.notice("Stopped.")
        elif stop_reason not in ("end_turn", None):
            self.transcript.notice(f"The agent ended its turn early ({stop_reason}).")
        self._set_state(ready=True, busy=False)

    def _on_update(self, update: Json) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk" and update["content"].get("type") == "text":
            self.transcript.assistant_chunk(update["content"]["text"])
        elif kind in ("tool_call", "tool_call_update"):
            self.transcript.tool(update)
        elif kind == "config_option_update" and self.client:
            self.composer.set_config_options(self.client.config_options)

    def _on_error(self, message: str) -> None:
        self.transcript.notice(message, error=True)
        App.Console.PrintError(f"ACP Chat: {message}\n")
        self._set_state(ready=bool(self.client and self.client.session_id), busy=False)

    def _set_state(self, ready: bool, busy: bool) -> None:
        self.composer.set_state(ready, busy)
        self.working.setVisible(busy)
        self._dots.start(450) if busy else self._dots.stop()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.proc:
            self.proc.kill()
        super().closeEvent(event)


class _SelectionObserver:
    def __init__(self, changed: Callable[[], None]) -> None:
        self.changed = changed

    def addSelection(self, *_args: object) -> None:
        self.changed()

    def removeSelection(self, *_args: object) -> None:
        self.changed()

    def setSelection(self, *_args: object) -> None:
        self.changed()

    def clearSelection(self, *_args: object) -> None:
        self.changed()


def _dock() -> ChatDock:
    mw = Gui.getMainWindow()
    dock: ChatDock | None = mw.findChild(QtWidgets.QDockWidget, "ACPChatDock")
    if dock is None:
        dock = ChatDock(mw)
        mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    return dock


class ToggleChatCommand:
    def GetResources(self) -> dict[str, str]:
        return {
            "Pixmap": ICON,
            "MenuText": "AI Chat",
            "ToolTip": "Show or hide the AI chat panel",
        }

    def Activated(self) -> None:
        dock = _dock()
        dock.setVisible(not dock.isVisible())  # agent starts on first show


class PreferencePage:
    """Edit > Preferences > AI Chat."""

    def __init__(self) -> None:
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle("General")
        self.folder = QtWidgets.QLineEdit()
        self.folder.setPlaceholderText("Folder of the open document, or your home folder")
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        note = QtWidgets.QLabel(
            "The agent starts in this folder, so it picks up the CLAUDE.md or AGENTS.md you keep there. "
            "Leave it empty to use the open document's folder. Takes effect on the next new chat."
        )
        note.setWordWrap(True)
        box = QtWidgets.QGroupBox("Working folder")
        inner = QtWidgets.QVBoxLayout(box)
        inner.addLayout(row)
        inner.addWidget(note)
        layout = QtWidgets.QVBoxLayout(self.form)
        layout.addWidget(box)
        layout.addStretch()

    def _browse(self) -> None:
        start = _working_folder() or os.path.expanduser("~")
        folder = QtWidgets.QFileDialog.getExistingDirectory(self.form, "Working folder", start)
        if folder:
            self.folder.setText(folder)

    def loadSettings(self) -> None:
        self.folder.setText(PREFS.GetString("WorkingFolder", ""))

    def saveSettings(self) -> None:
        PREFS.SetString("WorkingFolder", self.folder.text().strip())


class _ToolbarManipulator:
    def modifyToolBars(self) -> list[dict[str, str]]:
        # First entry creates the toolbar under the root, second adds the command.
        return [{"append": "AI Chat", "toolBar": ""}, {"append": "ACPChat_Toggle", "toolBar": "AI Chat"}]


Gui.addCommand("ACPChat_Toggle", ToggleChatCommand())
with open(ICON, "rb") as _f:  # the Preferences dialog looks up its group icon by this name
    Gui.addIcon("preferences-ai_chat", _f.read(), "SVG")
Gui.addPreferencePage(PreferencePage, "AI Chat")
Gui.addWorkbenchManipulator(_ToolbarManipulator())

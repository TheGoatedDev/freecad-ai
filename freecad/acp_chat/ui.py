"""Chat widgets: transcript (user turns, markdown replies, tool rows, inline
permission cards) and composer. Colours come from the active Qt palette so the
panel follows whatever FreeCAD theme is loaded."""

import base64
import json

from PySide import QtCore, QtGui, QtSvg, QtWidgets

# 16px line icons, 1.5 stroke, drawn in the current text colour.
_ICONS = {
    "send": '<path d="M8 13V3M3.5 7.5 8 3l4.5 4.5"/>',
    "stop": '<rect x="4.5" y="4.5" width="7" height="7" rx="1.5" fill="currentColor"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "pointer": '<path d="M3.5 2.5 12.5 7 8.6 8.6 7 12.5Z"/>',
    "camera": '<rect x="1.75" y="4.25" width="12.5" height="9" rx="2"/><circle cx="8" cy="8.75" r="2.4"/>'
    '<path d="M5.5 4.25 6.5 2.5h3l1 1.75"/>',
    "code": '<path d="M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5"/>',
    "check": '<path d="M3 8.5l3 3 7-7"/>',
    "x": '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>',
    "alert": '<circle cx="8" cy="8" r="6.25"/><path d="M8 4.75v4M8 11.25v.01"/>',
    "chevron": '<path d="M6 4l4 4-4 4"/>',
    "dot": '<circle cx="8" cy="8" r="3" fill="currentColor"/>',
}


def icon(name, color, size=16):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>'
    ).replace("currentColor", color)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    pm = QtGui.QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    renderer.render(p, QtCore.QRectF(0, 0, size, size))
    p.end()
    return QtGui.QIcon(pm)


def _mix(a, b, t):
    return QtGui.QColor.fromRgbF(*(x * t + y * (1 - t) for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3]))).name()


def theme(parent):
    """Colours as the active theme renders them. FreeCAD themes restyle widgets with
    QSS that can contradict the QPalette, so sample real styled widgets instead."""
    probe = QtWidgets.QWidget(parent)
    probe.setAttribute(QtCore.Qt.WA_StyledBackground)
    probe.resize(8, 8)
    window = probe.grab().toImage().pixelColor(4, 4)
    label = QtWidgets.QLabel("x", parent)
    label.ensurePolished()
    text = label.palette().color(QtGui.QPalette.WindowText)
    probe.deleteLater()
    label.deleteLater()
    pal = QtWidgets.QApplication.instance().palette()
    dark = window.lightnessF() < 0.5
    return {
        "text": text.name(),
        "window": window.name(),
        "muted": _mix(text, window, 0.62),
        "panel": _mix(text, window, 0.06),
        "border": _mix(text, window, 0.18),
        "accent": pal.color(QtGui.QPalette.Highlight).name(),
        "accent_text": pal.color(QtGui.QPalette.HighlightedText).name(),
        "error": "#ff7a7a" if dark else "#c62828",
        "mono": QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont).family(),
    }


def stylesheet(t):
    return """
#chatRoot, #transcriptBody {{ background: {window}; color: {text}; }}
#chatRoot QLabel, #chatRoot QToolButton {{ background: transparent; color: {text}; }}
#header {{ border-bottom: 1px solid {border}; }}
#title {{ font-weight: 600; }}
#chatRoot QLabel[muted="true"] {{ color: {muted}; }}
#chatRoot QLabel[error="true"] {{ color: {error}; }}
QScrollArea {{ border: none; background: transparent; }}
#userMsg {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
#toolRow {{ border: none; border-radius: 6px; text-align: left; padding: 3px 6px; color: {muted}; }}
#chatRoot QToolButton#toolRow {{ color: {muted}; }}
#chatRoot QToolButton#toolRow:hover, #chatRoot QToolButton#toolRow:checked {{ background: {panel}; color: {text}; }}
#toolDetail {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; padding: 4px;
              font-family: "{mono}"; font-size: 11px; color: {text}; }}
#permCard {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
#composer {{ background: {window}; border: 1px solid {border}; border-radius: 10px; }}
#composer[focus="true"] {{ border-color: {accent}; }}
#composer QPlainTextEdit {{ border: none; background: transparent; color: {text}; }}
#chip {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; }}
#chatRoot QPlainTextEdit {{ selection-background-color: {accent}; selection-color: {accent_text}; }}
#chatRoot QToolButton#iconBtn {{ border: none; border-radius: 6px; padding: 4px; }}
#chatRoot QToolButton#iconBtn:hover {{ background: {panel}; }}
QPushButton#sendBtn {{ background: {accent}; border: none; border-radius: 14px; padding: 0; }}
QPushButton#sendBtn:disabled {{ background: {border}; }}
QPushButton#primary {{ background: {accent}; color: {accent_text}; border: none; border-radius: 6px; padding: 5px 12px; }}
QPushButton#secondary {{ background: transparent; color: {text}; border: 1px solid {border}; border-radius: 6px;
                        padding: 4px 12px; }}
QPushButton#secondary:hover {{ background: {panel}; }}
""".format(**t)


def _label(text="", muted=False, fmt=QtCore.Qt.PlainText):
    lab = QtWidgets.QLabel(text)
    lab.setTextFormat(fmt)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.LinksAccessibleByMouse)
    lab.setOpenExternalLinks(True)
    if muted:
        lab.setProperty("muted", True)
    return lab


def _thumb(image_block, height=44):
    pm = QtGui.QPixmap()
    pm.loadFromData(base64.b64decode(image_block["data"]))
    return pm.scaledToHeight(height, QtCore.Qt.SmoothTransformation)


def _tool_title(update):
    title = update.get("title") or "tool"
    return title.split("__")[-1] if title.startswith("mcp__") else title


class ToolRow(QtWidgets.QWidget):
    """One agent tool call: status icon + name + first line of input; click to expand."""

    def __init__(self, t, update):
        super().__init__()
        self.t, self.state = t, {"status": "pending"}
        self.button = QtWidgets.QToolButton()
        self.button.setObjectName("toolRow")
        self.button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.button.setCheckable(True)
        self.detail = QtWidgets.QPlainTextEdit()
        self.detail.setObjectName("toolDetail")
        self.detail.setReadOnly(True)
        self.detail.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.detail.setAccessibleName("Tool input and output")
        self.detail.hide()
        self.button.toggled.connect(self._toggle)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.button)
        lay.addWidget(self.detail)
        self.update_from(update)

    def update_from(self, update):
        self.state.update({k: v for k, v in update.items() if v is not None})
        s = self.state
        code = (s.get("rawInput") or {}).get("code") if isinstance(s.get("rawInput"), dict) else None
        first = (code or "").strip().splitlines()[0][:60] if code and code.strip() else ""
        self.button.setText(f"{_tool_title(s)}   {first}".rstrip())
        name, color = {
            "completed": ("check", self.t["muted"]),
            "failed": ("x", self.t["error"]),
        }.get(s["status"], ("dot", self.t["accent"]))
        self.button.setIcon(icon(name, color, 14))
        status = {"completed": "done", "failed": "failed"}.get(s["status"], "running")
        self.button.setAccessibleName(f"Tool {_tool_title(s)}, {status}. Activate to show details")
        out = "\n".join(
            c["content"].get("text", "")
            for c in s.get("content") or []
            if c.get("type") == "content" and isinstance(c.get("content"), dict)
        )
        raw = code if code is not None else json.dumps(s.get("rawInput"), indent=2) if s.get("rawInput") else ""
        self.detail.setPlainText("\n\n".join(part for part in (raw.strip(), out.strip() and "→ " + out.strip()) if part))
        self._fit()

    def _toggle(self, open_):
        self.detail.setVisible(open_)
        QtCore.QTimer.singleShot(0, self._fit)  # wrapped height is known only once laid out

    def _fit(self):
        lines = self.detail.document().size().height() + 1
        self.detail.setFixedHeight(int(min(240, lines * self.detail.fontMetrics().lineSpacing() + 12)))


class PermissionCard(QtWidgets.QFrame):
    def __init__(self, params, reply):
        super().__init__()
        self.setObjectName("permCard")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        title = _tool_title(params.get("toolCall") or {})
        lay.addWidget(_label(f"The agent wants to run {title}."))
        grid = QtWidgets.QGridLayout()  # two per row so the card fits a narrow dock
        self.buttons = []
        options = params.get("options", [])
        primary = next((o["optionId"] for o in options if o.get("kind") == "allow_once"), None)
        for i, opt in enumerate(options):
            b = QtWidgets.QPushButton(opt["name"])
            b.setObjectName("primary" if opt["optionId"] == primary else "secondary")
            b.clicked.connect(lambda _=False, o=opt: self._choose(reply, o))
            grid.addWidget(b, i // 2, i % 2)
            self.buttons.append(b)
        lay.addLayout(grid)
        self.result = _label(muted=True)
        self.result.hide()
        lay.addWidget(self.result)

    def _choose(self, reply, option):
        for b in self.buttons:
            b.setEnabled(False)
        self.result.setText(f"You chose: {option['name']}")
        self.result.show()
        reply(option["optionId"])


class Transcript(QtWidgets.QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        body.setObjectName("transcriptBody")
        self.lay = QtWidgets.QVBoxLayout(body)
        self.lay.setContentsMargins(12, 12, 12, 12)
        self.lay.setSpacing(10)
        self.lay.addStretch()
        self.setWidget(body)
        self.t = theme(self)
        self.tools = {}
        self._assistant = None
        self._stick = True
        self.verticalScrollBar().valueChanged.connect(self._track_bottom)
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self._stick and self._to_bottom())
        self.clear()

    def _track_bottom(self, value):
        bar = self.verticalScrollBar()
        self._stick = value >= bar.maximum() - 24

    def _to_bottom(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _add(self, widget):
        if self.empty is not None:
            self.empty.deleteLater()
            self.empty = None
        self.lay.insertWidget(self.lay.count() - 1, widget)
        return widget

    def clear(self):
        while self.lay.count() > 1:
            self.lay.takeAt(0).widget().deleteLater()
        self.tools, self._assistant = {}, None
        self.empty = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(self.empty)
        lay.setContentsMargins(8, 40, 8, 8)
        head = _label("Ask about your model")
        head.setStyleSheet("font-weight: 600;")
        lay.addWidget(head)
        lay.addWidget(_label(
            "The agent can read and change the open document. Select faces or edges, "
            "then attach them so it knows what you mean.", muted=True))
        self.lay.insertWidget(0, self.empty)

    def user(self, text, attachments):
        box = QtWidgets.QFrame()
        box.setObjectName("userMsg")
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(_label(text))
        if attachments:
            row = QtWidgets.QHBoxLayout()
            for label, block in attachments:
                if block["type"] == "image":
                    pic = QtWidgets.QLabel()
                    pic.setPixmap(_thumb(block))
                    pic.setToolTip(label)
                    row.addWidget(pic)
                else:
                    chip = _label(label, muted=True)
                    chip.setToolTip(block.get("text", ""))
                    row.addWidget(chip)
            row.addStretch()
            lay.addLayout(row)
        self._assistant = None
        self._stick = True
        self._add(box)

    def assistant_chunk(self, text):
        if self._assistant is None:
            self._assistant = self._add(_label(fmt=QtCore.Qt.MarkdownText))
            self._assistant.raw = ""
        self._assistant.raw += text
        self._assistant.setText(self._assistant.raw)

    def tool(self, update):
        row = self.tools.get(update.get("toolCallId"))
        if row is None:
            row = self.tools[update.get("toolCallId")] = self._add(ToolRow(self.t, update))
            self._assistant = None
        else:
            row.update_from(update)

    def notice(self, text, error=False):
        row = QtWidgets.QHBoxLayout()
        pic = QtWidgets.QLabel()
        pic.setPixmap(icon("alert", self.t["error" if error else "muted"], 14).pixmap(14, 14))
        lab = _label(text, muted=not error)
        if error:
            lab.setProperty("error", True)
        row.addWidget(pic, 0, QtCore.Qt.AlignTop)
        row.addWidget(lab, 1)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self._assistant = None
        self._add(holder)

    def permission(self, params, reply):
        self._assistant = None
        self._stick = True
        self._add(PermissionCard(params, reply))


class Composer(QtWidgets.QFrame):
    send = QtCore.Signal(str)
    stop = QtCore.Signal()
    attach_selection = QtCore.Signal()
    attach_view = QtCore.Signal()

    def __init__(self, t):
        super().__init__()
        self.setObjectName("composer")
        self.t = t
        self.attachments = []
        self.busy = False
        self.chips = QtWidgets.QHBoxLayout()
        self.chips.setSpacing(6)
        self.edit = QtWidgets.QPlainTextEdit()
        self.edit.setPlaceholderText("Ask anything about the model")
        self.edit.setAccessibleName("Message")
        self.edit.setTabChangesFocus(True)
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self._grow)
        self.sel_btn = self._icon_btn("pointer", "Attach selection: send the selected faces, edges and objects")
        self.view_btn = self._icon_btn("camera", "Attach view: send a picture of the current 3D view")
        self.sel_btn.clicked.connect(self.attach_selection)
        self.view_btn.clicked.connect(self.attach_view)
        self.send_btn = QtWidgets.QPushButton()
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(28, 28)
        self.send_btn.clicked.connect(self._send_or_stop)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(2)
        for w in (self.sel_btn, self.view_btn):
            bottom.addWidget(w)
        bottom.addStretch()
        bottom.addWidget(self.send_btn)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 6)
        lay.setSpacing(4)
        lay.addLayout(self.chips)
        lay.addWidget(self.edit)
        lay.addLayout(bottom)
        self._grow()
        self.set_state(ready=False, busy=False)

    def _icon_btn(self, name, tip):
        b = QtWidgets.QToolButton()
        b.setObjectName("iconBtn")
        b.setIcon(icon(name, self.t["muted"]))
        b.setToolTip(tip)
        b.setAccessibleName(tip.split(":")[0])
        return b

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if not event.modifiers() & QtCore.Qt.ShiftModifier:
                self._send_or_stop(from_key=True)
                return True
        elif event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.FocusOut):
            self.setProperty("focus", event.type() == QtCore.QEvent.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return super().eventFilter(obj, event)

    def _grow(self):
        lines = max(1, min(8, int(self.edit.document().size().height())))
        self.edit.setFixedHeight(lines * self.edit.fontMetrics().lineSpacing() + 10)

    def _send_or_stop(self, from_key=False):
        if self.busy:
            if not from_key:
                self.stop.emit()
            return
        text = self.edit.toPlainText().strip()
        if text and self.ready:
            self.edit.clear()
            self.send.emit(text)

    def set_state(self, ready, busy):
        self.ready, self.busy = ready, busy
        self.send_btn.setEnabled(ready or busy)
        name = "stop" if busy else "send"
        self.send_btn.setIcon(icon(name, self.t["accent_text"] if (ready or busy) else self.t["muted"]))
        tip = "Stop the agent" if busy else "Send (Enter). Shift+Enter for a new line"
        self.send_btn.setToolTip(tip)
        self.send_btn.setAccessibleName("Stop" if busy else "Send")
        self.edit.setPlaceholderText("Ask anything about the model" if ready or busy else "Starting agent…")

    def add_attachment(self, label, block):
        self.attachments.append((label, block))
        self._render_chips()

    def take_attachments(self):
        out, self.attachments = self.attachments, []
        self._render_chips()
        return out

    def _render_chips(self):
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (label, block) in enumerate(self.attachments):
            chip = QtWidgets.QFrame()
            chip.setObjectName("chip")
            row = QtWidgets.QHBoxLayout(chip)
            row.setContentsMargins(4, 2, 2, 2)
            row.setSpacing(4)
            if block["type"] == "image":
                pic = QtWidgets.QLabel()
                pic.setPixmap(_thumb(block, 20))
                row.addWidget(pic)
            row.addWidget(QtWidgets.QLabel(label))
            rm = self._icon_btn("x", f"Remove {label}")
            rm.setIconSize(QtCore.QSize(12, 12))
            rm.clicked.connect(lambda _=False, i=i: (self.attachments.pop(i), self._render_chips()))
            row.addWidget(rm)
            self.chips.addWidget(chip)
        self.chips.addStretch()

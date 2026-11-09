"""Chat widgets: transcript (user turns, markdown replies, tool rows, inline
permission cards) and composer. Colours come from the active Qt palette so the
panel follows whatever FreeCAD theme is loaded."""

import base64
import json
from collections.abc import Callable
from typing import Any

from PySide import QtCore, QtGui, QtSvg, QtWidgets

from freecad.acp_chat.acp import Json, Reply

Theme = dict[str, str]

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
    "shield": '<path d="M8 1.75 13.25 3.75v4c0 3.2-2.2 5.6-5.25 6.5-3.05-.9-5.25-3.3-5.25-6.5v-4Z"/>',
    "sparkle": '<path d="M8 2c.6 3.2 1.8 4.4 5 5-3.2.6-4.4 1.8-5 5-.6-3.2-1.8-4.4-5-5 3.2-.6 4.4-1.8 5-5Z"/>',
    "chevron_down": '<path d="M4.5 6.5 8 10l3.5-3.5"/>',
    "bolt": '<path d="M9 1.75 3.75 9h4l-1 5.25L12.25 7h-4Z"/>',
}


def icon(name: str, color: str, size: int = 16) -> QtGui.QIcon:
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


def toggle_icon(name: str, off_color: str, on_color: str) -> QtGui.QIcon:
    out = QtGui.QIcon()
    for color, state in ((off_color, QtGui.QIcon.Off), (on_color, QtGui.QIcon.On)):
        out.addPixmap(icon(name, color).pixmap(16, 16), QtGui.QIcon.Normal, state)
    return out


def _mix(a: QtGui.QColor, b: QtGui.QColor, t: float) -> str:
    name: str = QtGui.QColor.fromRgbF(
        *(x * t + y * (1 - t) for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3], strict=True))
    ).name()
    return name


def theme(parent: QtWidgets.QWidget) -> Theme:
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
        "accent_soft": _mix(pal.color(QtGui.QPalette.Highlight), window, 0.18),
        "accent_text": pal.color(QtGui.QPalette.HighlightedText).name(),
        "error": "#ff7a7a" if dark else "#c62828",
        "mono": QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont).family(),
    }


def stylesheet(t: Theme) -> str:
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
#chatRoot QToolButton#iconBtn:checked {{ background: {accent_soft}; }}
#chatRoot QToolButton#pill {{ border: none; border-radius: 6px; padding: 3px 6px; color: {muted}; }}
#chatRoot QToolButton#pill:hover {{ background: {panel}; color: {text}; }}
#chatRoot QToolButton#pill::menu-indicator {{ image: none; width: 0; }}
QPushButton#sendBtn {{ background: {accent}; border: none; border-radius: 14px; padding: 0; }}
QPushButton#sendBtn:disabled {{ background: {border}; }}
QPushButton#primary {{ background: {accent}; color: {accent_text}; border: none; border-radius: 6px;
                      padding: 5px 12px; }}
QPushButton#secondary {{ background: transparent; color: {text}; border: 1px solid {border}; border-radius: 6px;
                        padding: 4px 12px; }}
QPushButton#secondary:hover {{ background: {panel}; }}
""".format(**t)


def _label(text: str = "", muted: bool = False, fmt: Any = QtCore.Qt.PlainText) -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(text)
    lab.setTextFormat(fmt)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.LinksAccessibleByMouse)
    lab.setOpenExternalLinks(True)
    if muted:
        lab.setProperty("muted", True)
    return lab


def _thumb(image_block: Json, height: int = 44) -> QtGui.QPixmap:
    pm = QtGui.QPixmap()
    pm.loadFromData(base64.b64decode(image_block["data"]))
    return pm.scaledToHeight(height, QtCore.Qt.SmoothTransformation)


def _tool_title(update: Json) -> str:
    title: str = update.get("title") or "tool"
    return title.split("__")[-1] if title.startswith("mcp__") else title


class ToolRow(QtWidgets.QWidget):
    """One agent tool call: status icon + name + first line of input; click to expand."""

    def __init__(self, t: Theme, update: Json) -> None:
        super().__init__()
        self.t = t
        self._text = ""
        self.state: Json = {"status": "pending"}
        self.button = QtWidgets.QToolButton()
        self.button.setObjectName("toolRow")
        self.button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        # Ignored: long code lines must not widen the transcript; text is elided to fit instead.
        self.button.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
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

    def update_from(self, update: Json) -> None:
        self.state.update({k: v for k, v in update.items() if v is not None})
        s = self.state
        code = (s.get("rawInput") or {}).get("code") if isinstance(s.get("rawInput"), dict) else None
        first = (code or "").strip().splitlines()[0][:60] if code and code.strip() else ""
        self._text = f"{_tool_title(s)}   {first}".rstrip()
        self._elide()
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
        self.detail.setPlainText(
            "\n\n".join(part for part in (raw.strip(), out.strip() and "→ " + out.strip()) if part)
        )
        self._fit()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        room = self.button.width() - self.button.iconSize().width() - 24
        self.button.setText(self.button.fontMetrics().elidedText(self._text, QtCore.Qt.ElideRight, max(room, 40)))
        self.button.setToolTip(self._text)

    def _toggle(self, open_: bool) -> None:
        self.detail.setVisible(open_)
        QtCore.QTimer.singleShot(0, self._fit)  # wrapped height is known only once laid out

    def _fit(self) -> None:
        lines = self.detail.document().size().height() + 1
        self.detail.setFixedHeight(int(min(240, lines * self.detail.fontMetrics().lineSpacing() + 12)))


class PermissionCard(QtWidgets.QFrame):
    """Inline question with one button per option; options are ACP-shaped
    ({optionId, name, kind}) and reply(optionId) is called once."""

    def __init__(self, text: str, options: list[Json], reply: Callable[[str], None]) -> None:
        super().__init__()
        self.setObjectName("permCard")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(_label(text))
        grid = QtWidgets.QGridLayout()  # two per row so the card fits a narrow dock
        self.buttons: list[QtWidgets.QPushButton] = []
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

    def _choose(self, reply: Callable[[str], None], option: Json) -> None:
        for b in self.buttons:
            b.setEnabled(False)
        self.result.setText(f"You chose: {option['name']}")
        self.result.show()
        reply(option["optionId"])


class Transcript(QtWidgets.QScrollArea):
    def __init__(self) -> None:
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
        self.tools: dict[str, ToolRow] = {}
        self._assistant: QtWidgets.QLabel | None = None
        self._raw = ""
        self._stick = True
        self.empty: QtWidgets.QWidget | None = None
        self.verticalScrollBar().valueChanged.connect(self._track_bottom)
        self.verticalScrollBar().rangeChanged.connect(self._on_range)
        self.clear()

    def _track_bottom(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._stick = value >= bar.maximum() - 24

    def _on_range(self, *_range: int) -> None:
        if self._stick:
            self._to_bottom()

    def _to_bottom(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _add(self, widget: Any) -> Any:
        if self.empty is not None:
            self.empty.deleteLater()
            self.empty = None
        self.lay.insertWidget(self.lay.count() - 1, widget)
        return widget

    def clear(self) -> None:
        while self.lay.count() > 1:
            self.lay.takeAt(0).widget().deleteLater()
        self.tools, self._assistant = {}, None
        self.empty = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(self.empty)
        lay.setContentsMargins(8, 40, 8, 8)
        head = _label("Ask about your model")
        head.setStyleSheet("font-weight: 600;")
        lay.addWidget(head)
        lay.addWidget(
            _label(
                "The agent can read and change the open document. Whatever you select is "
                "sent with your message, so it knows what you mean.",
                muted=True,
            )
        )
        self.lay.insertWidget(0, self.empty)

    def user(self, text: str, attachments: list[tuple[str, Json]]) -> None:
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

    def assistant_chunk(self, text: str) -> None:
        if self._assistant is None:
            self._assistant = self._add(_label(fmt=QtCore.Qt.MarkdownText))
            self._raw = ""
        self._raw += text
        self._assistant.setText(self._raw)

    def tool(self, update: Json) -> None:
        key = str(update.get("toolCallId"))
        row = self.tools.get(key)
        if row is None:
            row = self.tools[key] = self._add(ToolRow(self.t, update))
            self._assistant = None
        else:
            row.update_from(update)

    def notice(self, text: str, error: bool = False) -> None:
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

    def permission(self, params: Json, reply: Reply) -> None:
        self._assistant = None
        self._stick = True
        title = _tool_title(params.get("toolCall") or {})
        self._add(PermissionCard(f"The agent wants to run {title}.", params.get("options", []), reply))

    def sign_in(self, agent: str, methods: list[Json], choose: Callable[[str], None]) -> None:
        self._assistant = None
        self._stick = True
        options = [
            {"optionId": m["id"], "name": m["name"], "kind": "allow_once" if i == 0 else ""}
            for i, m in enumerate(methods)
        ]
        text = f"{agent} needs you to sign in. Pick how:"
        self._add(PermissionCard(text, options, choose))


def pill(name: str) -> QtWidgets.QToolButton:
    """Flat button that opens a menu, for compact pickers."""
    b = QtWidgets.QToolButton()
    b.setObjectName("pill")
    b.setAccessibleName(name)
    b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    b.setMenu(QtWidgets.QMenu(b))
    b.menu().setToolTipsVisible(True)
    return b


class Composer(QtWidgets.QFrame):
    """Input box. The selection and view toggles are read at send time; while on,
    their chips preview what will be sent."""

    MAX_CHIPS = 2

    send = QtCore.Signal(str)
    stop = QtCore.Signal()
    toggles_changed = QtCore.Signal()
    config_changed = QtCore.Signal(str, str)  # ACP config option id, value
    unselect = QtCore.Signal(object)  # one item from set_selection()

    def __init__(self, t: Theme) -> None:
        super().__init__()
        self.setObjectName("composer")
        self.t = t
        self.selection: list[tuple[str, tuple[str, str, str]]] = []  # (label, key) for the live preview
        self.busy = False
        self.ready = False
        self.chips = QtWidgets.QGridLayout()  # two per row so chips never widen a narrow dock
        self.chips.setSpacing(6)
        self.chips.setColumnStretch(2, 1)
        self.edit = QtWidgets.QPlainTextEdit()
        self.edit.setPlaceholderText("Ask anything about the model")
        self.edit.setAccessibleName("Message")
        self.edit.setTabChangesFocus(True)
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self._grow)
        self.sel_btn = self._toggle("pointer", "Include selection: send the selected faces, edges and objects")
        self.view_btn = self._toggle("camera", "Include view: send a picture of the 3D view")
        self.send_btn = QtWidgets.QPushButton()
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(28, 28)
        self.send_btn.clicked.connect(self._send_or_stop)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(2)
        for w in (self.sel_btn, self.view_btn):
            bottom.addWidget(w)
        bottom.addStretch()
        self.perm_btn = self._pill("Permission mode")
        self.model_btn = self._pill("Model, effort and speed")
        bottom.addWidget(self.perm_btn)
        bottom.addWidget(self.model_btn)
        bottom.addSpacing(4)
        bottom.addWidget(self.send_btn)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 6)
        lay.setSpacing(4)
        lay.addLayout(self.chips)
        lay.addWidget(self.edit)
        lay.addLayout(bottom)
        self._grow()
        self.set_state(ready=False, busy=False)

    def _icon_btn(self, name: str, tip: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton()
        b.setObjectName("iconBtn")
        b.setIcon(icon(name, self.t["muted"]))
        b.setToolTip(tip)
        b.setAccessibleName(tip.split(":")[0])
        return b

    def _toggle(self, name: str, tip: str) -> QtWidgets.QToolButton:
        b = self._icon_btn(name, tip)
        b.setCheckable(True)
        b.setIcon(toggle_icon(name, self.t["muted"], self.t["accent"]))
        b.toggled.connect(self._on_toggled)
        return b

    def _on_toggled(self, _on: bool) -> None:
        self._render_chips()
        self.toggles_changed.emit()

    def _pill(self, name: str) -> QtWidgets.QToolButton:
        b = pill(name)
        b.hide()
        return b

    def _select_menu(self, menu: QtWidgets.QMenu, opt: Json) -> None:
        """Exclusive checkable entries for one select option."""
        group = QtGui.QActionGroup(menu)
        for choice in opt.get("options", []):
            act = menu.addAction(choice["name"])
            act.setCheckable(True)
            act.setChecked(choice["value"] == opt.get("currentValue"))
            act.setToolTip(choice.get("description", ""))
            act.triggered.connect(lambda _=False, i=opt["id"], v=choice["value"]: self.config_changed.emit(i, v))
            group.addAction(act)

    def set_config_options(self, options: list[Json]) -> None:
        """Permission pill: the `mode` option. Model pill: the model list, then
        thought-level and model-config options (on/off ones become toggles)."""
        selects = [o for o in options if o.get("type") == "select"]

        def current(opt: Json) -> Json:
            return next((c for c in opt.get("options", []) if c["value"] == opt.get("currentValue")), {})

        mode = next((o for o in selects if o.get("category") == "mode"), None)
        self.perm_btn.setVisible(mode is not None)
        if mode:
            self.perm_btn.menu().clear()
            self._select_menu(self.perm_btn.menu(), mode)
            self.perm_btn.setText(current(mode).get("name", ""))
            self.perm_btn.setIcon(icon("shield", self.t["muted"], 14))
            self.perm_btn.setToolTip(f"Permissions: {current(mode).get('description') or current(mode).get('name')}")

        model = next((o for o in selects if o.get("category") == "model"), None)
        extras = [o for o in selects if o.get("category") in ("thought_level", "model_config")]
        self.model_btn.setVisible(bool(model or extras))
        menu = self.model_btn.menu()
        menu.clear()
        if model:
            self._select_menu(menu, model)
        if extras:
            menu.addSeparator()
        tip: list[str] = []
        fast = False
        for opt in extras:
            values = {c["value"] for c in opt.get("options", [])}
            if values == {"on", "off"}:
                on = opt.get("currentValue") == "on"
                fast = fast or (opt["id"] == "fast" and on)
                act = menu.addAction(opt["name"])
                act.setCheckable(True)
                act.setChecked(on)
                act.setToolTip(opt.get("description", ""))
                act.triggered.connect(
                    lambda checked, i=opt["id"]: self.config_changed.emit(i, "on" if checked else "off")
                )
            else:
                sub = menu.addMenu(f"{opt['name']}: {current(opt).get('name', '')}")
                sub.setToolTipsVisible(True)
                self._select_menu(sub, opt)
            tip.append(f"{opt['name']}: {current(opt).get('name', opt.get('currentValue'))}")
        if model:
            name = current(model).get("name", "")
            # "Default (recommended)" says less than the model it stands for
            label = str(current(model).get("description") or name) if name.startswith("Default") else name
            self.model_btn.setText(label if len(label) <= 16 else label[:15] + "…")
            tip.insert(0, f"Model: {label}")
        self.model_btn.setIcon(icon("bolt" if fast else "sparkle", self.t["accent"] if fast else self.t["muted"], 14))
        self.model_btn.setToolTip("\n".join(tip))

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.KeyPress and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if not event.modifiers() & QtCore.Qt.ShiftModifier:
                self._send_or_stop(from_key=True)
                return True
        elif event.type() in (QtCore.QEvent.FocusIn, QtCore.QEvent.FocusOut):
            self.setProperty("focus", event.type() == QtCore.QEvent.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return bool(super().eventFilter(obj, event))

    def _grow(self) -> None:
        lines = max(1, min(8, int(self.edit.document().size().height())))
        self.edit.setFixedHeight(lines * self.edit.fontMetrics().lineSpacing() + 10)

    def _send_or_stop(self, from_key: bool = False) -> None:
        if self.busy:
            if not from_key:
                self.stop.emit()
            return
        text = self.edit.toPlainText().strip()
        if text and self.ready:
            self.edit.clear()
            self.send.emit(text)

    def set_state(self, ready: bool, busy: bool) -> None:
        self.ready, self.busy = ready, busy
        self.send_btn.setEnabled(ready or busy)
        name = "stop" if busy else "send"
        self.send_btn.setIcon(icon(name, self.t["accent_text"] if (ready or busy) else self.t["muted"]))
        tip = "Stop the agent" if busy else "Send (Enter). Shift+Enter for a new line"
        self.send_btn.setToolTip(tip)
        self.send_btn.setAccessibleName("Stop" if busy else "Send")
        self.edit.setPlaceholderText("Ask anything about the model" if ready or busy else "Starting agent…")

    def set_selection(self, items: list[tuple[str, tuple[str, str, str]]]) -> None:
        self.selection = items
        self._render_chips()

    def _chip(self, text: str, tip: str, on_remove: Callable[..., object] | None, pic: str | None = None) -> None:
        chip = QtWidgets.QFrame()
        chip.setObjectName("chip")
        chip.setToolTip(tip)
        row = QtWidgets.QHBoxLayout(chip)
        row.setContentsMargins(6, 2, 2, 2)
        row.setSpacing(4)
        if pic:
            p = QtWidgets.QLabel()
            p.setPixmap(icon(pic, self.t["muted"], 12).pixmap(12, 12))
            row.addWidget(p)
        row.addWidget(QtWidgets.QLabel(text))
        if on_remove:
            rm = self._icon_btn("x", f"Remove {text}")
            rm.setIconSize(QtCore.QSize(12, 12))
            rm.clicked.connect(on_remove)
            row.addWidget(rm)
        n = self.chips.count()
        self.chips.addWidget(chip, n // 2, n % 2, QtCore.Qt.AlignLeft)

    def _render_chips(self) -> None:
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self.view_btn.isChecked():
            self._chip(
                "3D view",
                "A picture of the 3D view is taken when you send",
                lambda: self.view_btn.setChecked(False),
                pic="camera",
            )
        if self.sel_btn.isChecked():
            shown = self.selection[: self.MAX_CHIPS]
            for label, key in shown:
                self._chip(
                    label,
                    "Click × to deselect it in FreeCAD",
                    lambda _=False, k=key: self.unselect.emit(k),
                    pic="pointer",
                )
            rest = self.selection[self.MAX_CHIPS :]
            if rest:
                self._chip(f"+{len(rest)} more", "\n".join(label for label, _k in rest), None)
            if not self.selection:
                self._chip("Nothing selected", "Select faces, edges or objects in the 3D view", None, pic="pointer")

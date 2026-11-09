"""Turn the user's FreeCAD selection and 3D view into ACP prompt content blocks."""

import base64
import os
import tempfile
from typing import Any

import FreeCADGui as Gui

MAX_VIEW_SIDE = 1280


def _v(p: Any) -> str:
    return f"({p.x:.3f}, {p.y:.3f}, {p.z:.3f})"


def _geometry(sub: Any) -> str:
    """Short geometric summary of a sub-shape so the agent can locate it."""
    kind = getattr(sub, "ShapeType", None)
    if kind == "Face":
        surf = sub.Surface
        u0, u1, v0, v1 = sub.ParameterRange
        parts = [type(surf).__name__, f"area {sub.Area:.3f} mm²", f"centroid {_v(sub.CenterOfMass)}"]
        if type(surf).__name__ == "Plane":
            parts.append(f"normal {_v(sub.normalAt((u0 + u1) / 2, (v0 + v1) / 2))}")
        if hasattr(surf, "Radius"):
            parts.append(f"radius {surf.Radius:.3f}")
        if hasattr(surf, "Axis") and type(surf).__name__ != "Plane":
            parts.append(f"axis {_v(surf.Axis)} through {_v(surf.Center)}")
        return ", ".join(parts)
    if kind == "Edge":
        curve = type(sub.Curve).__name__
        parts = [curve, f"length {sub.Length:.3f}", f"from {_v(sub.Vertexes[0].Point)} to {_v(sub.Vertexes[-1].Point)}"]
        if hasattr(sub.Curve, "Radius"):
            parts.append(f"radius {sub.Curve.Radius:.3f}, center {_v(sub.Curve.Center)}")
        return ", ".join(parts)
    if kind == "Vertex":
        return f"point {_v(sub.Point)}"
    return str(kind or type(sub).__name__)


def selection_items() -> list[tuple[str, tuple[str, str, str]]]:
    """[(chip label, (doc, object, sub-element))] for the current selection."""
    items: list[tuple[str, tuple[str, str, str]]] = []
    for sel in Gui.Selection.getSelectionEx("", 0):
        obj = sel.Object
        for name in sel.SubElementNames or [""]:
            leaf = name.split(".")[-1]
            items.append((f"{leaf} · {obj.Label}" if leaf else obj.Label, (sel.DocumentName, obj.Name, name)))
    return items


def selection_text() -> str | None:
    """Describe the current selection, or return None if nothing is selected."""
    lines: list[str] = []
    for sel in Gui.Selection.getSelectionEx():
        obj = sel.Object
        head = f"{sel.DocumentName}.{obj.Name} (label '{obj.Label}', {obj.TypeId})"
        names = sel.SubElementNames
        if not names:
            lines.append(f"- {head}")
        points = list(sel.PickedPoints) + [None] * len(names)
        for name, sub, pt in zip(names, sel.SubObjects, points, strict=False):
            try:
                geo = _geometry(sub)
            except Exception as e:  # non-Part objects, degenerate shapes
                geo = f"(no geometry: {e})"
            picked = f", picked at {_v(pt)}" if pt else ""
            lines.append(f"- {name} of {head}: {geo}{picked}")
    if not lines:
        return None
    return "The user selected these elements in FreeCAD (mm):\n" + "\n".join(lines)


def view_image() -> str | None:
    """PNG of the active 3D view as base64, or None if there is no 3D view."""
    gui_doc = Gui.ActiveDocument
    view = gui_doc and gui_doc.ActiveView
    if view is None or not hasattr(view, "saveImage"):
        return None
    w, h = view.getSize()
    scale = min(1.0, MAX_VIEW_SIDE / max(w, h, 1))
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        view.saveImage(path, int(w * scale), int(h * scale), "Current")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    finally:
        os.remove(path)

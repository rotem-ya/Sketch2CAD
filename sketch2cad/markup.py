"""Hand markings on the drawing preview (free pen / straight lines) to explain a requested change.

The browser canvas (``markup_canvas``) sends only the strokes, in coordinates normalised to the image
(0..1); ``compose`` draws them on the preview so the marked image can be sent with the request.

    stroke = {"tool": "pen" | "line", "color": "red" | "blue" | "green", "width": 0.004,
              "points": [[x, y], ...]}            # x, y in 0..1 of the image width / height
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

COLORS = {"red": (229, 57, 53), "blue": (30, 136, 229), "green": (67, 160, 71)}
TOOLS = ("pen", "line")
MAX_STROKES = 500
MAX_POINTS = 5000
CANVAS_SIDE = 1600              # image size in the browser canvas
SEND_SIDE = 2000                # marked image sent to the model


def clean_strokes(strokes) -> list[dict]:
    """Validate strokes from the browser: known tools/colours, points clipped to 0..1, sane sizes."""
    out = []
    for s in (strokes or [])[:MAX_STROKES]:
        if not isinstance(s, dict) or s.get("tool") not in TOOLS:
            continue
        pts = []
        for p in (s.get("points") or [])[:MAX_POINTS]:
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            pts.append([min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)])
        if len(pts) < 2:
            continue
        if s["tool"] == "line":
            pts = [pts[0], pts[-1]]
        try:
            width = min(max(float(s.get("width", 0.004)), 0.001), 0.02)
        except (TypeError, ValueError):
            width = 0.004
        out.append({"tool": s["tool"], "color": s.get("color") if s.get("color") in COLORS else "red",
                    "width": width, "points": pts})
    return out


def _fit(img, max_side: int):
    side = max(img.size)
    if side > max_side:
        img = img.resize((round(img.width * max_side / side), round(img.height * max_side / side)))
    return img


def canvas_image(preview: Path | str, max_side: int = CANVAS_SIDE) -> str:
    """The preview as a JPEG data URL for the browser canvas."""
    from PIL import Image
    with Image.open(preview) as im:
        img = _fit(im.convert("RGB"), max_side)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.standard_b64encode(buf.getvalue()).decode("ascii")


def compose(preview: Path | str, strokes, out: Path | str, max_side: int = SEND_SIDE) -> Path:
    """Draw the strokes on the preview and save it as PNG (at most `max_side` px)."""
    from PIL import Image, ImageDraw
    with Image.open(preview) as im:
        img = _fit(im.convert("RGB"), max_side)
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for s in clean_strokes(strokes):
        pts = [(x * w, y * h) for x, y in s["points"]]
        width = max(2, round(s["width"] * w))
        draw.line(pts, fill=COLORS[s["color"]], width=width, joint="curve")
        r = width / 2                                        # round ends
        for x, y in (pts[0], pts[-1]):
            draw.ellipse((x - r, y - r, x + r, y + r), fill=COLORS[s["color"]])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


# ------------------------------------------------------------------ browser canvas (Streamlit component)
_CSS = """
.mk-root { font-family: var(--st-font, sans-serif); }
.mk-bar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
.mk-bar button { border: 1px solid #c9ccd1; background: #fff; border-radius: 6px; padding: 4px 10px;
                 cursor: pointer; font-size: 14px; color: #262730; }
.mk-bar button.on { border-color: #ff4b4b; background: #fff0f0; font-weight: 600; }
.mk-bar .sw { width: 22px; height: 22px; padding: 0; border-radius: 50%; }
.mk-bar .sw.on { outline: 3px solid #262730; outline-offset: 1px; }
.mk-bar .count { margin-inline-start: auto; font-size: 13px; color: #6b6f76; }
.mk-root canvas { width: 100%; height: auto; display: block; border: 1px solid #c9ccd1; border-radius: 6px;
                  touch-action: none; cursor: crosshair; background: #fff; }
"""

_JS = """
export default function (component) {
  const { data, setStateValue, parentElement } = component;
  const COLORS = { red: "#e53935", blue: "#1e88e5", green: "#43a047" };
  let root = parentElement.querySelector(".mk-root");
  if (!root) {
    root = document.createElement("div");
    root.className = "mk-root";
    parentElement.appendChild(root);
    root._s = { strokes: [], tool: "pen", color: "red", src: null, img: null, current: null };
    root.innerHTML = `<div class="mk-bar">
        <button data-tool="pen"></button><button data-tool="line"></button>
        <button class="sw" data-color="red"></button><button class="sw" data-color="blue"></button>
        <button class="sw" data-color="green"></button>
        <button data-act="undo"></button><button data-act="clear"></button>
        <span class="count"></span></div><canvas></canvas>`;
    const s = root._s, canvas = root.querySelector("canvas"), ctx = canvas.getContext("2d");
    const send = () => setStateValue("strokes", s.strokes.map(x => ({ ...x, points: x.points.slice() })));
    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      return [Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
              Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1)];
    };
    root._draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (s.img) ctx.drawImage(s.img, 0, 0, canvas.width, canvas.height);
      const all = s.current ? [...s.strokes, s.current] : s.strokes;
      for (const st of all) {
        ctx.strokeStyle = COLORS[st.color] || COLORS.red;
        ctx.lineWidth = Math.max(2, st.width * canvas.width);
        ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.beginPath();
        st.points.forEach(([x, y], i) => {
          const px = x * canvas.width, py = y * canvas.height;
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        });
        ctx.stroke();
      }
      root.querySelector(".count").textContent = s.strokes.length ? `${s.strokes.length} ${s.labels.count}` : "";
      root.querySelectorAll("[data-tool]").forEach(b => b.classList.toggle("on", b.dataset.tool === s.tool));
      root.querySelectorAll("[data-color]").forEach(b => b.classList.toggle("on", b.dataset.color === s.color));
    };
    root.querySelectorAll("[data-color]").forEach(b => { b.style.background = COLORS[b.dataset.color]; });
    root.querySelectorAll("[data-tool]").forEach(b => b.onclick = () => { s.tool = b.dataset.tool; root._draw(); });
    root.querySelectorAll("[data-color]").forEach(b => b.onclick = () => { s.color = b.dataset.color; root._draw(); });
    root.querySelector('[data-act="undo"]').onclick = () => { s.strokes.pop(); root._draw(); send(); };
    root.querySelector('[data-act="clear"]').onclick = () => { s.strokes = []; root._draw(); send(); };
    canvas.addEventListener("pointerdown", (e) => {
      canvas.setPointerCapture(e.pointerId);
      const p = pos(e);
      s.current = { tool: s.tool, color: s.color, width: 0.004, points: [p, p] };
      root._draw();
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!s.current) return;
      const p = pos(e);
      if (s.current.tool === "line") s.current.points[1] = p; else s.current.points.push(p);
      root._draw();
    });
    const finish = () => {
      if (!s.current) return;
      const pts = s.current.points, a = pts[0], b = pts[pts.length - 1];
      const moved = pts.some(([x, y]) => Math.hypot(x - a[0], y - a[1]) > 0.002) || Math.hypot(b[0] - a[0], b[1] - a[1]) > 0.002;
      if (moved) s.strokes.push(s.current);
      s.current = null; root._draw();
      if (moved) send();
    };
    canvas.addEventListener("pointerup", finish);
    canvas.addEventListener("pointercancel", finish);
  }
  const s = root._s, canvas = root.querySelector("canvas");
  s.labels = data.labels;
  root.dir = data.rtl ? "rtl" : "ltr";
  root.querySelector('[data-tool="pen"]').textContent = "✏️ " + data.labels.pen;
  root.querySelector('[data-tool="line"]').textContent = "📏 " + data.labels.line;
  root.querySelector('[data-act="undo"]').textContent = "↶ " + data.labels.undo;
  root.querySelector('[data-act="clear"]').textContent = "🗑 " + data.labels.clear;
  for (const c of ["red", "blue", "green"]) root.querySelector(`[data-color="${c}"]`).title = data.labels[c];
  if (data.image !== s.src) {
    s.src = data.image;
    const img = new Image();
    img.onload = () => { s.img = img; canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; root._draw(); };
    img.src = data.image;
  } else {
    root._draw();
  }
}
"""

def markup_canvas(preview: Path | str, *, key: str, labels: dict, rtl: bool = False) -> list[dict]:
    """Show the preview with drawing tools; returns the current strokes (cleaned).

    The strokes live in the browser and are sent after each change; a new `key` starts a clean canvas.
    """
    import streamlit as st
    # registered on every run: the registry belongs to the Streamlit runtime, not to this module (an
    # identical re-registration is a silent no-op)
    component = st.components.v2.component("sketch2cad_markup", html="", css=_CSS, js=_JS)
    result = component(key=key, data={"image": canvas_image(preview), "labels": labels, "rtl": rtl},
                        default={"strokes": []}, on_strokes_change=lambda: None)
    value = result.get("strokes") if hasattr(result, "get") else getattr(result, "strokes", None)
    return clean_strokes(value)

"""Buat gambar dan GIF demo untuk README (docs/). Memakai data contoh, bukan config pengguna."""
import io
import math
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
os.environ["APPDATA"] = tempfile.mkdtemp()  # jangan sentuh config asli
src = ROOT / "orbit.pyw"
fw = {"__name__": "orbit", "__file__": str(src)}
exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), fw)

from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QTabWidget

app = QApplication(sys.argv)
OUT = ROOT / "docs"
OUT.mkdir(exist_ok=True)

home = Path.home()
DEMO = [("Documents", "Documents"), ("Downloads", "Downloads"), ("Projects", "Documents"), ("Music", "Music"),
        ("Pictures", "Pictures"), ("Videos", "Videos"), ("Desktop", "Desktop"), ("Notes.txt", None)]
slots = []
for name, real in DEMO:
    fake = rf"C:\Users\you\{name}" if real else r"C:\Users\you\Documents\Notes.txt"
    # ikon diambil dari folder asli yang setara, path yang ditampilkan tetap contoh
    real_path = home / real if real else Path(sys.executable)
    fw["_icon_cache"][fake] = fw["path_icon"](str(real_path))
    slots.append({"name": name, "path": fake})
fw["_icon_cache"][r"C:\Users\you\Documents\Notes.txt"] = fw["QFileIconProvider"]().icon(
    fw["QFileIconProvider"].IconType.File).pixmap(40, 40)
PAGES = [{"name": "Main", "slots": slots}, {"name": "Work", "slots": slots[:5]}]
TITLE = "Orbit"


def to_pil(qimg):
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    qimg.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def backdrop(p, w, h, dim=0.0):
    g = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    g.setColorAt(0, QColor(58, 84, 130))
    g.setColorAt(0.55, QColor(120, 96, 140))
    g.setColorAt(1, QColor(196, 128, 104))
    p.fillRect(QRectF(0, 0, w, h), QBrush(g))
    # jendela tiruan supaya terlihat seperti desktop
    for x, y, ww, hh in ((40, 50, 300, 190), (380, 210, 290, 170)):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(QRectF(x, y, ww, hh), 10, 10)
        p.setBrush(QColor(255, 255, 255, 55))
        p.drawRoundedRect(QRectF(x, y, ww, 26), 10, 10)
    if dim:
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, int(80 * dim)))


def cursor(p, x, y):
    path = QPainterPath()
    path.moveTo(x, y)
    for dx, dy in ((0, 20), (5, 15), (9, 24), (12, 22), (8, 14), (15, 14)):
        path.lineTo(x + dx, y + dy)
    path.closeSubpath()
    p.setPen(QPen(QColor(0, 0, 0), 1.2))
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(path)


def badge(p, w, h, text, sub=""):
    f = QFont("Segoe UI", 13, QFont.Bold)
    p.setFont(f)
    tw = p.fontMetrics().horizontalAdvance(text) + 36
    r = QRectF((w - tw) / 2, h - 58, tw, 38)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(15, 15, 18, 220))
    p.drawRoundedRect(r, 19, 19)
    p.setPen(QColor(255, 255, 255))
    p.drawText(r, Qt.AlignCenter, text)


def frame(w, h, t=0.0, hover=None, page=0, cur=None, caption="", theme=None, dim=True):
    img = QImage(w, h, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    backdrop(p, w, h, t if dim else 0)
    if t > 0:
        p.save()
        p.setOpacity(t)
        p.translate(w / 2, h / 2 - 12)
        s = (0.88 + 0.12 * t) * 0.78
        p.scale(s, s)
        fw["paint_wheel"](p, PAGES, page, hover, TITLE, theme or fw["theme_from_preset"]("Midnight Blue"))
        p.restore()
    if cur:
        cursor(p, *cur)
    if caption:
        badge(p, w, h, caption)
    p.end()
    return to_pil(img)


def hover_at(dx, dy, n):
    if math.hypot(dx, dy) <= fw["R_IN"] * 0.78:
        return None
    step = 360 / n
    return int(((math.degrees(math.atan2(dy, dx)) + 90 + step / 2) % 360) // step)


# --- GIF demo
W, H = 720, 470
cx, cy = W / 2, H / 2 - 12
frames, durations = [], []


def add(img, ms):
    frames.append(img)
    durations.append(ms)


add(frame(W, H, cur=(cx, cy), caption="Hold Alt + Q"), 900)
for i in range(1, 7):
    add(frame(W, H, t=i / 6, cur=(cx, cy), caption="Hold Alt + Q"), 30)
add(frame(W, H, t=1, cur=(cx, cy), caption="Move the mouse toward a folder"), 700)
# kursor berputar melewati beberapa irisan
for k in range(0, 34):
    ang = math.radians(-90 + k * 7)
    r = 150
    x, y = cx + math.cos(ang) * r, cy + math.sin(ang) * r
    add(frame(W, H, t=1, hover=hover_at(x - cx, y - cy, len(slots)), cur=(x, y),
              caption="Move the mouse toward a folder"), 55)
x, y = cx + math.cos(math.radians(148)) * 150, cy + math.sin(math.radians(148)) * 150
hv = hover_at(x - cx, y - cy, len(slots))
add(frame(W, H, t=1, hover=hv, cur=(x, y), caption=f"Release Alt + Q to open {slots[hv]['name']}"), 1200)
for i in range(5, -1, -1):
    add(frame(W, H, t=i / 5, hover=hv, cur=(x, y), caption=f"Release Alt + Q to open {slots[hv]['name']}"), 30)
add(frame(W, H, cur=(x, y), caption=f"{slots[hv]['name']} opens in Explorer"), 1000)
# batal: lepas di tengah
for i in range(1, 7):
    add(frame(W, H, t=i / 6, cur=(cx, cy), caption="Release in the center to cancel"), 30)
add(frame(W, H, t=1, cur=(cx, cy), caption="Release in the center to cancel"), 1300)
for i in range(5, -1, -1):
    add(frame(W, H, t=i / 5, cur=(cx, cy), caption="Release in the center to cancel"), 30)
add(frame(W, H, cur=(cx, cy), caption="Nothing opens"), 900)
# scroll ganti halaman
for i in range(1, 7):
    add(frame(W, H, t=i / 6, cur=(cx, cy), caption="Scroll to switch pages"), 30)
add(frame(W, H, t=1, page=0, cur=(cx, cy), caption="Scroll to switch pages"), 900)
add(frame(W, H, t=1, page=1, cur=(cx, cy), caption="Scroll to switch pages"), 1300)
for i in range(5, -1, -1):
    add(frame(W, H, t=i / 5, page=1, cur=(cx, cy), caption="Scroll to switch pages"), 30)

pal = [f.quantize(colors=200, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
pal[0].save(OUT / "demo.gif", save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)

# --- gambar utama
frame(900, 600, t=1, hover=1, cur=None, dim=True).save(OUT / "wheel.png")

# --- deretan tema
names = ["Dark", "Midnight Blue", "Neon Purple", "Crimson", "Emerald", "Gold",
         "Sakura", "Cyberpunk", "Dracula", "Nord", "Mint", "Light"]
cell = 250
sheet = QImage(cell * 6, cell * 2 + 0, QImage.Format_ARGB32)
p = QPainter(sheet)
p.setRenderHint(QPainter.Antialiasing)
p.setRenderHint(QPainter.SmoothPixmapTransform)
backdrop(p, sheet.width(), sheet.height())
for i, name in enumerate(names):
    x, y = (i % 6) * cell, (i // 6) * cell
    p.save()
    p.translate(x + cell / 2, y + cell / 2 + 10)
    s = (cell - 40) / (2 * (fw["R_OUT"] + 12))
    p.scale(s, s)
    fw["paint_wheel"](p, PAGES, 0, 1, "", fw["theme_from_preset"](name))
    p.restore()
    p.setPen(QColor(255, 255, 255))
    p.setFont(QFont("Segoe UI", 10, QFont.Bold))
    p.drawText(QRectF(x, y + 4, cell, 20), Qt.AlignHCenter, name)
p.end()
to_pil(sheet).save(OUT / "themes.png")

# --- jendela Setting
cfg = fw["default_config"]()
cfg["wheels"][0].update(title=TITLE, pages=PAGES, theme=fw["theme_from_preset"]("Midnight Blue"))
cfg["wheels"].append(fw["new_wheel"]("Work", {"mods": ["alt"], "vk": 0x57}, "Work", slots[:4]))
hook = fw["HotkeyHook"]()
hook.paused = True
dlg = fw["SettingsDialog"](cfg, hook, fw["make_icon"]())
dlg.resize(900, 640)
dlg.show()
app.processEvents()
to_pil(dlg.grab().toImage()).save(OUT / "settings-slots.png")
dlg.findChildren(QTabWidget)[1].setCurrentIndex(1)
app.processEvents()
to_pil(dlg.grab().toImage()).save(OUT / "settings-appearance.png")
dlg.close()

for f in sorted(OUT.iterdir()):
    print(f.name, round(f.stat().st_size / 1024), "KB")

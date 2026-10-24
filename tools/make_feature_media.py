"""Create README media for the newer features: the Open windows wheel, folders opening as Explorer tabs,
and the Setting options behind them. Uses mock windows and sample data, never the user's own windows."""
import glob
import io
import math
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
os.environ["APPDATA"] = tempfile.mkdtemp()  # never touch the real config
src = ROOT / "orbit.pyw"
fw = {"__name__": "orbit", "__file__": str(src)}
exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), fw)

from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QTabWidget

app = QApplication(sys.argv)
OUT = ROOT / "docs"
OUT.mkdir(exist_ok=True)
W, H = 720, 470
CX, CY = W / 2, H / 2 - 12
SCALE = 0.78


def first_existing(*patterns):
    for pattern in patterns:
        hits = glob.glob(os.path.expandvars(pattern))
        if hits:
            return hits[0]
    return ""


# Sample windows. Icons come from programs installed on this PC when present; the titles are made up.
APPS = {
    "chrome": first_existing(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    "explorer": r"C:\Windows\explorer.exe",
    "blender": first_existing(r"C:\Program Files\Blender Foundation\*\blender.exe"),
    "notepad": r"C:\Windows\System32\notepad.exe",
    "obs": first_existing(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"),
    "code": first_existing(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
}
MOCK_WINDOWS = [  # most recently used first
    ("chrome", "Orbit - GitHub - Google Chrome"),
    ("explorer", "Downloads - File Explorer"),
    ("blender", "scene.blend - Blender"),
    ("notepad", "notes.txt - Notepad"),
    ("obs", "OBS Studio - Profile: Stream"),
    ("code", "orbit.pyw - Visual Studio Code"),
]
WIN_SLOTS = [{"name": title, "path": APPS[app], "detail": Path(APPS[app]).name if APPS[app] else app}
             for app, title in MOCK_WINDOWS]


def demo_window_pages(order="recent"):
    slots = sorted(WIN_SLOTS, key=lambda s: s["name"].casefold()) if order == "name" else list(WIN_SLOTS)
    return [{"name": "Open windows", "slots": slots}]


fw["window_pages"] = demo_window_pages  # the Setting preview uses this instead of the real windows

home = Path.home()
FOLDER_SLOTS = []
for name, real in (("Documents", "Documents"), ("Downloads", "Downloads"), ("Projects", "Documents"),
                   ("Music", "Music"), ("Pictures", "Pictures"), ("Videos", "Videos"), ("Desktop", "Desktop")):
    fake = rf"C:\Users\you\{name}"
    fw["_icon_cache"][fake] = fw["path_icon"](str(home / real))
    FOLDER_SLOTS.append({"name": name, "path": fake})
FOLDER_PAGES = [{"name": "Main", "slots": FOLDER_SLOTS}]


def to_pil(qimg):
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    qimg.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")


def backdrop(p, dim=0.0):
    g = QLinearGradient(QPointF(0, 0), QPointF(W, H))
    g.setColorAt(0, QColor(58, 84, 130))
    g.setColorAt(0.55, QColor(120, 96, 140))
    g.setColorAt(1, QColor(196, 128, 104))
    p.fillRect(QRectF(0, 0, W, H), QBrush(g))
    if dim:
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(80 * dim)))


def cursor(p, x, y):
    path = QPainterPath()
    path.moveTo(x, y)
    for dx, dy in ((0, 20), (5, 15), (9, 24), (12, 22), (8, 14), (15, 14)):
        path.lineTo(x + dx, y + dy)
    path.closeSubpath()
    p.setPen(QPen(QColor(0, 0, 0), 1.2))
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(path)


def badge(p, text):
    p.setFont(QFont("Segoe UI", 13, QFont.Bold))
    tw = p.fontMetrics().horizontalAdvance(text) + 36
    r = QRectF((W - tw) / 2, H - 58, tw, 38)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(15, 15, 18, 220))
    p.drawRoundedRect(r, 19, 19)
    p.setPen(QColor(255, 255, 255))
    p.drawText(r, Qt.AlignCenter, text)


def wheel(p, pages, hover, title, theme, t):
    if t <= 0:
        return
    p.save()
    p.setOpacity(t)
    p.translate(CX, CY)
    s = (0.88 + 0.12 * t) * SCALE
    p.scale(s, s)
    fw["paint_wheel"](p, pages, 0, hover, title, fw["theme_from_preset"](theme))
    p.restore()


def hover_at(x, y, n):
    dx, dy = x - CX, y - CY
    if math.hypot(dx, dy) <= fw["R_IN"] * SCALE:
        return None
    step = 360 / n
    return int(((math.degrees(math.atan2(dy, dx)) + 90 + step / 2) % 360) // step)


def slot_point(i, n, r=150):
    a = math.radians(-90 + i * 360 / n)
    return CX + math.cos(a) * r, CY + math.sin(a) * r


def save_gif(frames, durations, name):
    # dithering keeps the background gradient from banding in the 256-color GIF palette
    pal = [f.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG) for f in frames]
    pal[0].save(OUT / name, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)


# ---------------------------------------------------------------- mock app windows

def app_window(p, rect, app, title, lift=0.0):
    """A simplified dark app window with its program icon and title; lift adds a focus glow."""
    x, y, w, h = rect
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 90 + int(60 * lift)))
    p.drawRoundedRect(QRectF(x + 4, y + 8, w, h), 10, 10)
    p.setBrush(QColor(32, 32, 34))
    p.setPen(QPen(QColor(120, 160, 255, int(200 * lift)) if lift else QColor(70, 70, 70), 1.5 if lift else 1))
    p.drawRoundedRect(QRectF(x, y, w, h), 8, 8)
    icon = fw["slot_icon"]({"path": APPS[app]})
    p.drawPixmap(QRectF(x + 10, y + 8, 16, 16), icon, QRectF(icon.rect()))
    p.setPen(QColor(230, 230, 230))
    p.setFont(QFont("Segoe UI", 8))
    p.drawText(QRectF(x + 32, y + 4, w - 120, 24), Qt.AlignVCenter, title)
    p.setPen(QPen(QColor(190, 190, 190), 1.1))
    for i, bx in enumerate((x + w - 86, x + w - 56, x + w - 26)):
        if i == 0:
            p.drawLine(QPointF(bx, y + 16), QPointF(bx + 9, y + 16))
        elif i == 1:
            p.drawRect(QRectF(bx, y + 11, 9, 9))
        else:
            p.drawLine(QPointF(bx, y + 11), QPointF(bx + 9, y + 20))
            p.drawLine(QPointF(bx + 9, y + 11), QPointF(bx, y + 20))
    body = QRectF(x + 1, y + 30, w - 2, h - 31)
    p.setPen(Qt.NoPen)
    if app == "chrome":
        p.setBrush(QColor(245, 246, 248))
        p.drawRect(body)
        p.setBrush(QColor(36, 41, 47))
        p.drawRect(QRectF(body.x(), body.y(), body.width(), 26))
        p.setBrush(QColor(210, 214, 220))
        for i in range(5):
            p.drawRoundedRect(QRectF(body.x() + 20, body.y() + 42 + i * 22, body.width() * (0.75 - i * 0.08), 10), 4, 4)
    elif app == "blender":
        p.setBrush(QColor(48, 48, 48))
        p.drawRect(body)
        p.setBrush(QColor(62, 62, 62))
        p.drawRect(QRectF(body.x() + 8, body.y() + 8, body.width() - 90, body.height() - 16))
        p.setBrush(QColor(232, 125, 13))
        p.drawEllipse(QPointF(body.x() + (body.width() - 90) / 2, body.y() + body.height() / 2), 30, 30)
        p.setBrush(QColor(72, 72, 72))
        p.drawRect(QRectF(body.right() - 74, body.y() + 8, 66, body.height() - 16))
    else:  # explorer
        p.setBrush(QColor(28, 28, 28))
        p.drawRect(QRectF(body.x(), body.y(), 90, body.height()))
        folder = fw["path_icon"](str(home / "Documents"))
        for i in range(6):
            fx, fy = body.x() + 110 + (i % 3) * 80, body.y() + 18 + (i // 3) * 70
            p.drawPixmap(QRectF(fx + 14, fy, 36, 36), folder, QRectF(folder.rect()))
    p.restore()


DESK = {"chrome": (40, 40, 400, 250), "explorer": (250, 120, 420, 250), "blender": (130, 190, 430, 240)}


def desktop(p, order, lift_app=None, lift=0.0):
    """order: back to front"""
    for app in order:
        title = next(t for a, t in MOCK_WINDOWS if a == app)
        app_window(p, DESK[app], app, title, lift if app == lift_app else 0.0)


def windows_frame(order, t=0.0, hover=None, cur=None, caption="", lift_app=None, lift=0.0):
    img = QImage(W, H, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    backdrop(p)
    desktop(p, order, lift_app, lift)
    if t > 0:
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(80 * t)))
    wheel(p, demo_window_pages(), hover, "Windows", "Midnight Blue", t)
    if cur:
        cursor(p, *cur)
    if caption:
        badge(p, caption)
    p.end()
    return to_pil(img)


frames, durations = [], []


def add(img, ms):
    frames.append(img)
    durations.append(ms)


before = ["blender", "explorer", "chrome"]  # Chrome is in front
after = ["explorer", "chrome", "blender"]
n = len(WIN_SLOTS)
target = next(i for i, s in enumerate(WIN_SLOTS) if "Blender" in s["name"])
add(windows_frame(before, cur=(CX, CY), caption="Lots of windows open?"), 1100)
for i in range(1, 7):
    add(windows_frame(before, t=i / 6, cur=(CX, CY), caption="Hold Alt + W"), 30)
add(windows_frame(before, t=1, cur=(CX, CY), caption="Every open window, newest first"), 1300)
tx, ty = slot_point(target, n)
for k in range(1, 13):
    e = 1 - (1 - k / 12) ** 3
    x, y = CX + (tx - CX) * e, CY + (ty - CY) * e
    add(windows_frame(before, t=1, hover=hover_at(x, y, n), cur=(x, y), caption="Point at the one you want"), 40)
add(windows_frame(before, t=1, hover=target, cur=(tx, ty), caption="Release Alt + W"), 1000)
for i in range(5, -1, -1):
    add(windows_frame(before, t=i / 5, hover=target, cur=(tx, ty), caption="Release Alt + W"), 30)
for i in range(1, 7):
    add(windows_frame(after, cur=(tx, ty), caption="Blender comes to the front", lift_app="blender", lift=i / 6), 35)
add(windows_frame(after, cur=(tx, ty), caption="Blender comes to the front", lift_app="blender", lift=1), 1700)
save_gif(frames, durations, "demo-open-windows.gif")


# ---------------------------------------------------------------- folders as Explorer tabs

def explorer_with_tabs(p, tabs, active, grow=1.0):
    """Explorer window (Windows 11, dark) with a tab strip; the last tab widens in by `grow`."""
    ww, wh = 560, 300
    x0, y0 = (W - ww) / 2, 46
    p.save()
    p.translate(x0, y0)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 90))
    p.drawRoundedRect(QRectF(4, 8, ww, wh), 10, 10)
    p.setBrush(QColor(24, 24, 24))
    p.setPen(QPen(QColor(70, 70, 70), 1))
    p.drawRoundedRect(QRectF(0, 0, ww, wh), 8, 8)
    p.setPen(Qt.NoPen)
    tx = 8
    p.setFont(QFont("Segoe UI", 8))
    for i, name in enumerate(tabs):
        width = 180 * (grow if i == len(tabs) - 1 else 1.0)
        if width < 8:
            continue
        r = QRectF(tx, 6, width - 4, 30)
        if i == active:
            p.setBrush(QColor(44, 44, 44))
            p.drawRoundedRect(r, 6, 6)
        if width > 40:
            icon = fw["_icon_cache"][rf"C:\Users\you\{name}"]
            p.drawPixmap(QRectF(r.x() + 10, r.y() + 7, 16, 16), icon, QRectF(icon.rect()))
            p.setPen(QColor(235, 235, 235) if i == active else QColor(170, 170, 170))
            p.drawText(QRectF(r.x() + 32, r.y(), r.width() - 40, r.height()), Qt.AlignVCenter, name)
            p.setPen(Qt.NoPen)
        tx += width
    p.setPen(QColor(200, 200, 200))
    p.setFont(QFont("Segoe UI", 12))
    p.drawText(QRectF(tx + 4, 6, 28, 30), Qt.AlignCenter, "+")
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(44, 44, 44))
    p.drawRect(QRectF(0, 36, ww, 34))
    p.setBrush(QColor(56, 56, 56))
    p.drawRoundedRect(QRectF(80, 42, ww - 200, 22), 4, 4)
    p.setPen(QColor(220, 220, 220))
    p.setFont(QFont("Segoe UI", 8))
    p.drawText(QRectF(90, 42, ww - 220, 22), Qt.AlignVCenter, "  >  ".join(["This PC", "Users", "you", tabs[active]]))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(28, 28, 28))
    p.drawRect(QRectF(0, 70, 120, wh - 78))
    p.setPen(QColor(230, 230, 230))
    for i, nav in enumerate(("Home", "Desktop", "Downloads", "Documents", "Pictures", "Music")):
        y = 82 + i * 26
        if nav == tabs[active]:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(60, 60, 60))
            p.drawRoundedRect(QRectF(6, y - 3, 108, 22), 4, 4)
            p.setPen(QColor(230, 230, 230))
        p.drawText(QRectF(18, y - 3, 100, 22), Qt.AlignVCenter, nav)
    contents = {"Projects": ["Website", "App", "Thesis", "Video edit", "Assets", "Archive"],
                "Downloads": ["setup.exe", "photo.jpg", "invoice.pdf", "music.mp3", "notes.txt", "archive.zip"]}
    folder = fw["path_icon"](str(home / "Documents"))
    file_icon = fw["QFileIconProvider"]().icon(fw["QFileIconProvider"].IconType.File).pixmap(40, 40)
    for i, item in enumerate(contents[tabs[active]]):
        fx, fy = 150 + (i % 3) * 130, 92 + (i // 3) * 92
        icon = folder if tabs[active] == "Projects" else file_icon
        p.drawPixmap(QRectF(fx + 37, fy, 40, 40), icon, QRectF(icon.rect()))
        p.setPen(QColor(225, 225, 225))
        p.drawText(QRectF(fx, fy + 46, 114, 18), Qt.AlignHCenter, item)
    p.restore()


def tabs_frame(tabs, active, grow=1.0, t=0.0, hover=None, cur=None, caption=""):
    img = QImage(W, H, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    backdrop(p)
    explorer_with_tabs(p, tabs, active, grow)
    if t > 0:
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(80 * t)))
    wheel(p, FOLDER_PAGES, hover, "Orbit", "Midnight Blue", t)
    if cur:
        cursor(p, *cur)
    if caption:
        badge(p, caption)
    p.end()
    return to_pil(img)


frames, durations = [], []
n = len(FOLDER_SLOTS)
target = next(i for i, s in enumerate(FOLDER_SLOTS) if s["name"] == "Downloads")
tx, ty = slot_point(target, n)
one, two = ["Projects"], ["Projects", "Downloads"]
add(tabs_frame(one, 0, cur=(CX, CY + 60), caption="Explorer is already open"), 1100)
for i in range(1, 7):
    add(tabs_frame(one, 0, t=i / 6, cur=(CX, CY), caption="Hold Alt + Q"), 30)
for k in range(1, 11):
    e = 1 - (1 - k / 10) ** 3
    x, y = CX + (tx - CX) * e, CY + (ty - CY) * e
    add(tabs_frame(one, 0, t=1, hover=hover_at(x, y, n), cur=(x, y), caption="Pick a folder"), 40)
add(tabs_frame(one, 0, t=1, hover=target, cur=(tx, ty), caption="Release Alt + Q"), 900)
for i in range(5, -1, -1):
    add(tabs_frame(one, 0, t=i / 5, hover=target, cur=(tx, ty), caption="Release Alt + Q"), 30)
for i in range(1, 8):
    add(tabs_frame(two, 1, grow=i / 7, cur=(tx, ty), caption="It opens as a new tab"), 35)
add(tabs_frame(two, 1, cur=(tx, ty), caption="It opens as a new tab"), 1300)
add(tabs_frame(two, 1, cur=(tx, ty), caption="Setting > General > Open folders in"), 1500)
save_gif(frames, durations, "demo-explorer-tabs.gif")


# ---------------------------------------------------------------- Setting screenshots

fw["CONFIG_PATH"] = Path(r"C:\Users\you\AppData\Roaming\Orbit\config.json")  # shown on the General tab
cfg = fw["default_config"]()
cfg["wheels"][0].update(title="Orbit", pages=FOLDER_PAGES, theme=fw["theme_from_preset"]("Midnight Blue"))
windows_wheel = fw["new_wheel"]("Windows", {"mods": ["alt"], "vk": 0x57}, "Windows", kind="windows")
windows_wheel["theme"] = fw["theme_from_preset"]("Midnight Blue")
cfg["wheels"].append(windows_wheel)
cfg["folder_open"] = "tab"
hook = fw["HotkeyHook"]()
hook.paused = True
dlg = fw["SettingsDialog"](cfg, hook, fw["make_icon"]())
dlg.resize(900, 700)
dlg.show()
dlg.wheel_combo.setCurrentIndex(1)
app.processEvents()
dlg.preview.hover = 2
to_pil(dlg.grab().toImage()).save(OUT / "settings-open-windows.png")
dlg.findChildren(QTabWidget)[0].setCurrentIndex(1)
app.processEvents()
to_pil(dlg.grab().toImage()).save(OUT / "settings-general.png")
dlg.close()

for f in ("demo-open-windows.gif", "demo-explorer-tabs.gif", "settings-open-windows.png", "settings-general.png"):
    print(f, round((OUT / f).stat().st_size / 1024), "KB")

"""Orbit: a radial menu (wheel) that opens folders and files from a hotkey.

There can be several wheels, each with its own hotkey (no two may share one).
Hold the hotkey -> the wheel appears -> point the mouse at a slot -> release -> the folder/file opens.
Release with the cursor in the center (or press Esc / right-click) -> the wheel closes, nothing opens.
Scroll while the wheel is open -> switch pages.
"""
import copy
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

try:  # pywin32: talks to open File Explorer windows so folders can open as tabs
    import win32com.client
    import win32gui
except ImportError:  # running from source without pywin32: folders always open in a new window
    win32com = win32gui = None

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal, QFileInfo, QSize
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFileIconProvider,
    QColorDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox,
    QPushButton, QSlider, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

APP_NAME = "Orbit"
APP_VERSION = "1.0.3"
FROZEN = getattr(sys, "frozen", False)  # running as a PyInstaller exe
# program folder: where the exe (built version) or this .pyw file lives
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
LEGACY_NAME = "FolderWheel"  # this app's former name; its settings and autostart are migrated automatically
LEGACY_CONFIG_DIR = CONFIG_DIR.parent / LEGACY_NAME
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAX_SLOTS = 12

R_IN, R_OUT = 78, 215  # radius of the center circle (cancel zone) and of the wheel edge


# ---------------------------------------------------------------- config

def default_config():
    home = Path.home()
    slots = [
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Desktop", home / "Desktop"),
        ("Pictures", home / "Pictures"),
        ("Videos", home / "Videos"),
        ("Project", home / "Documents" / "Project"),
    ]
    return {
        "settings_hotkey": {"mods": ["alt", "shift"], "vk": 0x51},  # Alt+Shift+Q
        "position": "cursor",
        "dim": True,
        "folder_open": "window",  # "window": new Explorer window, "tab": new tab in an open Explorer window
        "open_page": "first",  # "first": every wheel starts on page 1, "last": resume the last page you used
        "wheels": [new_wheel("Wheel 1", {"mods": ["alt"], "vk": 0x51},  # Alt+Q
                             os.environ.get("USERNAME", "Orbit"),
                             [{"name": n, "path": str(p)} for n, p in slots])],
    }


# color presets: slice, highlighted slice, center circle
THEMES = {
    "Dark": ("#121216", "#f5f5f5", "#08080a"),
    "Light": ("#eeeef2", "#1e1e24", "#ffffff"),
    "Midnight Blue": ("#0f1a2e", "#4da3ff", "#0a1222"),
    "Neon Purple": ("#1a1028", "#b46bff", "#120a1c"),
    "Crimson": ("#1c0d0f", "#ff4d5e", "#140809"),
    "Emerald": ("#0d1c16", "#34d399", "#08140f"),
    "Gold": ("#15130d", "#f5c542", "#0d0b07"),
    "Ocean": ("#0b1f2a", "#22d3ee", "#06141c"),
    "Sunset": ("#2a1410", "#ff8a3d", "#1c0c08"),
    "Sakura": ("#2a1420", "#ff8fc7", "#1c0c15"),
    "Lavender": ("#1e1a2e", "#c4b5fd", "#141020"),
    "Mint": ("#e8f7f1", "#10b981", "#ffffff"),
    "Ice": ("#e6f0fa", "#2563eb", "#ffffff"),
    "Peach": ("#fbeee6", "#f97362", "#fffaf6"),
    "Cyberpunk": ("#120e1f", "#fcee0a", "#0a0714"),
    "Matrix": ("#050f07", "#22ff66", "#020803"),
    "Dracula": ("#282a36", "#ff79c6", "#1e1f29"),
    "Nord": ("#2e3440", "#88c0d0", "#242933"),
    "Monokai": ("#272822", "#a6e22e", "#1e1f1a"),
    "Coffee": ("#2b2018", "#d6a46b", "#1d150f"),
    "Forest": ("#14201a", "#8fbf5a", "#0c1510"),
    "Slate": ("#1f2933", "#e2e8f0", "#151c24"),
    "Mono": ("#101010", "#ffffff", "#000000"),
}
SIZES = {"Small": 0.8, "Medium": 1.0, "Large": 1.2}


def theme_from_preset(name, base=None):
    theme = dict(base or {"opacity": 0.85, "size": 1.0})
    theme["preset"] = name
    theme["slice"], theme["highlight"], theme["center"] = THEMES[name]
    return theme


WHEEL_TYPES = {"folders": "Folders and files", "windows": "Open windows"}


def new_wheel(name, hotkey, title, slots=(), kind="folders"):
    # title: text in the wheel center while no slot is highlighted
    # kind: "folders" holds the slots you set up; "windows" fills itself with the open windows each time
    return {"name": name, "type": kind, "order": "recent", "hotkey": hotkey, "title": title,
            "theme": theme_from_preset("Dark"),
            "pages": [{"name": "Main", "slots": list(slots)}]}


LOG_PATH = CONFIG_DIR / "orbit.log"


def log(msg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {msg}\n")
    except OSError:
        pass


BACKUP_PATH = CONFIG_DIR / "config.bak.json"


def slot_count(cfg):
    return sum(len(p["slots"]) for w in cfg["wheels"] for p in w["pages"])


def _read_config(path):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("unknown config format")
    if "wheels" not in cfg and isinstance(cfg.get("pages"), list):
        # old config format (single wheel): hotkey/title/pages at the top level
        default = default_config()["wheels"][0]
        cfg["wheels"] = [new_wheel("Wheel 1", cfg.pop("hotkey", default["hotkey"]),
                                   cfg.pop("title", default["title"]))]
        cfg["wheels"][0]["pages"] = cfg.pop("pages")
    if not isinstance(cfg.get("wheels"), list) or not cfg["wheels"]:
        raise ValueError("unknown config format")
    base = default_config()
    base.update(cfg)
    for w in base["wheels"]:
        if not w.get("pages"):
            w["pages"] = [{"name": "Main", "slots": []}]
        w["theme"] = {**theme_from_preset("Dark"), **w.get("theme", {})}
        if w.get("type") not in WHEEL_TYPES:
            w["type"] = "folders"
        if w.get("order") not in WINDOW_ORDERS:
            w["order"] = "recent"
    return base


def _migrate_legacy_config():
    """Copy settings from the old-name folder (FolderWheel) if Orbit has no settings of its own yet."""
    # check the file, not the folder: log() has already created the Orbit folder by now
    if CONFIG_PATH.exists() or BACKUP_PATH.exists() or not (LEGACY_CONFIG_DIR / "config.json").exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "config.bak.json"):
        if (LEGACY_CONFIG_DIR / name).exists():
            shutil.copy2(LEGACY_CONFIG_DIR / name, CONFIG_DIR / name)
    log(f"settings migrated from {LEGACY_CONFIG_DIR}")


def load_config(retries=12, delay=0.5, startup=False):
    """Return (config, status): "ok", "new" (no config yet) or "fallback".

    An existing config is never overwritten here. Right after signing in to Windows the file can be
    briefly unreadable; that used to start the app with default settings. Now it retries a few times,
    then tries the backup; if that fails too the result is "fallback" (defaults, in memory only) and
    the app keeps retrying in the background.
    """
    if startup:
        # right after sign-in even AppData may not be ready; wait before treating this as a fresh install
        for _ in range(retries):
            if CONFIG_PATH.exists() or BACKUP_PATH.exists() or (LEGACY_CONFIG_DIR / "config.json").exists():
                break
            time.sleep(delay)
    _migrate_legacy_config()
    if not CONFIG_PATH.exists() and not BACKUP_PATH.exists():
        cfg = default_config()
        save_config(cfg)
        log("load_config: no config yet, creating defaults")
        return cfg, "new"
    for attempt in range(retries):
        try:
            cfg = _read_config(CONFIG_PATH)
            log(f"load_config: ok (attempt {attempt + 1}), {slot_count(cfg)} slots")
            return cfg, "ok"
        except (OSError, ValueError) as e:
            log(f"load_config: attempt {attempt + 1} failed: {e!r}")
            time.sleep(delay)
    try:
        cfg = _read_config(BACKUP_PATH)
        log(f"load_config: using the backup, {slot_count(cfg)} slots")
        return cfg, "ok"
    except (OSError, ValueError) as e:
        log(f"load_config: backup failed too: {e!r}; using defaults for now")
        return default_config(), "fallback"


def save_config(cfg):
    log(f"save_config: {slot_count(cfg)} slots")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _read_config(CONFIG_PATH)  # only back up the old config if it is valid
        shutil.copy2(CONFIG_PATH, BACKUP_PATH)
    except (OSError, ValueError):
        pass
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)  # never leaves a half-written file behind


# Autostart uses a shortcut in the Startup folder. The registry Run key is no longer used: recent Windows 11
# builds only run Run entries already approved in the Startup apps list, so new entries are skipped at sign-in.
STARTUP_DIR = Path(os.environ.get("APPDATA", Path.home())) / r"Microsoft\Windows\Start Menu\Programs\Startup"
STARTUP_LNK = STARTUP_DIR / "Orbit.lnk"
LEGACY_LNK = STARTUP_DIR / "Folder Wheel.lnk"


STARTUP_ARG = "--startup"  # launched at sign-in: stay in the tray, never open a window


def autostart_target():
    """Return (program, arguments) for the autostart shortcut."""
    if FROZEN:
        return sys.executable, STARTUP_ARG
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw), f'"{Path(__file__).resolve()}" {STARTUP_ARG}'


def _remove_run_entry():
    removed = False
    for name in (APP_NAME, LEGACY_NAME):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
                winreg.QueryValueEx(k, name)
                winreg.DeleteValue(k, name)
                removed = True
        except OSError:
            pass
    return removed


def get_autostart():
    return STARTUP_LNK.exists()


def set_autostart(on):
    _remove_run_entry()
    if not on:
        STARTUP_LNK.unlink(missing_ok=True)
        return
    target, args = autostart_target()
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:FW_LNK); "
              "$s.TargetPath = $env:FW_TARGET; $s.Arguments = $env:FW_ARGS; "
              "$s.WorkingDirectory = $env:FW_DIR; $s.Description = 'Orbit'; $s.Save()")
    env = dict(os.environ, FW_LNK=str(STARTUP_LNK), FW_TARGET=target, FW_ARGS=args,
               FW_DIR=str(APP_DIR))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=env, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def migrate_autostart():
    """Older versions used the registry Run key or an old-name shortcut; move autostart to Orbit.lnk."""
    moved = _remove_run_entry()
    if LEGACY_LNK.exists():
        LEGACY_LNK.unlink(missing_ok=True)
        moved = True
    if moved:
        try:
            set_autostart(True)
        except (OSError, subprocess.CalledProcessError):
            pass


# ---------------------------------------------------------------- win32 hook

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.GetKeyNameTextW.argtypes = [wt.LONG, wt.LPWSTR, ctypes.c_int]
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_ssize_t  # HINSTANCE, used as a status code
user32.AllowSetForegroundWindow.argtypes = [wt.DWORD]
user32.SetForegroundWindow.argtypes = [wt.HWND]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x201, 0x202, 0x204, 0x205
WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP = 0x20A, 0x20B, 0x20C
VK_XBUTTON1, VK_XBUTTON2, VK_ESCAPE = 0x05, 0x06, 0x1B
MOD_VKS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x6F, 0x90}


def vk_name(vk):
    special = {VK_XBUTTON1: "Mouse Back", VK_XBUTTON2: "Mouse Forward", 0x20: "Space", 0x09: "Tab", 0x14: "CapsLock"}
    if vk in special:
        return special[vk]
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    lparam = user32.MapVirtualKeyW(vk, 0) << 16
    if vk in EXTENDED_VKS:
        lparam |= 1 << 24
    buf = ctypes.create_unicode_buffer(64)
    if user32.GetKeyNameTextW(lparam, buf, 64):
        return buf.value
    return f"VK {vk:#04x}"


def hotkey_label(hk):
    order = ["ctrl", "alt", "shift", "win"]
    parts = [m.capitalize() for m in order if m in hk.get("mods", [])]
    return " + ".join(parts + [vk_name(hk["vk"])])


def key_down(vk):
    return user32.GetAsyncKeyState(vk) & 0x8000


class HotkeyHook(QObject):
    """Global keyboard + mouse hooks (WH_*_LL) on their own thread.

    Hotkeys are swallowed (not passed on to other apps). While a wheel is open, left/right clicks,
    scrolling and Esc are swallowed too and sent to the wheel. The Setting hotkey is a single press.
    """

    pressed = Signal(int)  # index of the wheel whose hotkey was pressed
    released = Signal()
    cancel = Signal()
    clicked = Signal()
    scrolled = Signal(int)
    settings_requested = Signal()

    def __init__(self):
        super().__init__()
        self.wheel_keys = []      # [(vk, mods)] per wheel
        self.settings_vk = 0
        self.settings_mods = set()
        self._settings_held = False
        self.active = None        # (wheel index, vk) while a wheel hotkey is held
        self.wheel_open = False   # set by the GUI
        self.paused = False       # used while recording a new hotkey
        self._swallow_up = set()
        self._kb_proc = HOOKPROC(self._kb)
        self._ms_proc = HOOKPROC(self._ms)
        threading.Thread(target=self._run, daemon=True).start()

    def set_wheel_hotkeys(self, hks):
        self.wheel_keys = [(int(hk["vk"]), set(hk.get("mods", []))) for hk in hks]
        self.active = None

    def set_settings_hotkey(self, hk):
        self.settings_vk = int(hk["vk"])
        self.settings_mods = set(hk.get("mods", []))
        self._settings_held = False

    def _run(self):
        hmod = kernel32.GetModuleHandleW(None)
        self._hk = user32.SetWindowsHookExW(13, self._kb_proc, hmod, 0)  # WH_KEYBOARD_LL
        self._hm = user32.SetWindowsHookExW(14, self._ms_proc, hmod, 0)  # WH_MOUSE_LL
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    @staticmethod
    def _mods_match(mods):
        for name, vks in MOD_VKS.items():
            if bool(any(key_down(v) for v in vks)) != (name in mods):
                return False
        return True

    @staticmethod
    def _tap_mask_key():
        # dummy key so releasing Alt/Win does not open a menu bar / the Start menu
        user32.keybd_event(0xE8, 0, 0, 0)
        user32.keybd_event(0xE8, 0, 2, 0)

    def _wheel_key(self, vk, down):
        """Wheel hotkey (held). Return True if the event should be swallowed."""
        if self.active is not None:
            if vk != self.active[1]:
                return False
            if not down:
                self.active = None
                self.released.emit()
            return True  # including auto-repeat
        if not down:
            return False
        for i, (kvk, mods) in enumerate(self.wheel_keys):
            if kvk == vk and self._mods_match(mods):
                self.active = (i, vk)
                if mods & {"alt", "win"}:
                    self._tap_mask_key()
                self.pressed.emit(i)
                return True
        return False

    def _settings_key(self, vk, down):
        """Setting hotkey (single press). Return True if the event should be swallowed."""
        if vk != self.settings_vk or self.active is not None:
            return False
        if not down:
            held, self._settings_held = self._settings_held, False
            return held
        if self._settings_held:
            return True  # auto-repeat
        if not self._mods_match(self.settings_mods):
            return False
        self._settings_held = True
        # also makes this process the last input sender, so the Setting window may come to the front
        self._tap_mask_key()
        self.settings_requested.emit()
        return True

    def _kb(self, code, wparam, lparam):
        if code == 0 and not self.paused:
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            # injected input is still handled (mouse/macro software often sends keys via SendInput),
            # except our own 0xE8 dummy key
            if k.vkCode != 0xE8:
                down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self.wheel_open and k.vkCode == VK_ESCAPE:
                    if down:
                        self.cancel.emit()
                    return 1
                if self._settings_key(k.vkCode, down) or self._wheel_key(k.vkCode, down):
                    return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _ms(self, code, wparam, lparam):
        if code == 0 and not self.paused:
            m = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if wparam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                vk = VK_XBUTTON1 if (m.mouseData >> 16) == 1 else VK_XBUTTON2
                down = wparam == WM_XBUTTONDOWN
                if self._settings_key(vk, down) or self._wheel_key(vk, down):
                    return 1
            elif self.wheel_open and wparam == WM_MOUSEWHEEL:
                delta = ctypes.c_short(m.mouseData >> 16).value
                self.scrolled.emit(-1 if delta > 0 else 1)
                return 1
            elif self.wheel_open and wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                self._swallow_up.add(wparam + 1)
                (self.clicked if wparam == WM_LBUTTONDOWN else self.cancel).emit()
                return 1
            elif wparam in self._swallow_up:
                self._swallow_up.discard(wparam)
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)


# ---------------------------------------------------------------- wheel overlay

_icon_provider = None
_icon_cache = {}


def path_icon(path):
    global _icon_provider
    if _icon_provider is None:
        _icon_provider = QFileIconProvider()
    if path not in _icon_cache:
        info = QFileInfo(os.path.expandvars(path))
        if info.exists():
            icon = _icon_provider.icon(info)
        else:
            icon = QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_MessageBoxWarning)
        _icon_cache[path] = icon.pixmap(QSize(40, 40))
    return _icon_cache[path]


def ring_slice(r_in, r_out, a0, a1):
    """Ring slice from angle a0 to a1 (degrees, clockwise from 3 o'clock)."""
    p = QPainterPath()
    outer = QRectF(-r_out, -r_out, 2 * r_out, 2 * r_out)
    inner = QRectF(-r_in, -r_in, 2 * r_in, 2 * r_in)
    p.arcMoveTo(outer, -a0)
    p.arcTo(outer, -a0, -(a1 - a0))
    p.arcTo(inner, -a1, a1 - a0)
    p.closeSubpath()
    return p


class DimOverlay(QWidget):
    """Full-screen dimming layer. It is a flat color, so it is faded via window opacity
    (cheap for Windows) instead of being repainted per pixel every frame."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: black;")
        self.setWindowOpacity(0.0)


WHEEL_SIZE = 2 * (R_OUT + 24)


class WheelOverlay(QWidget):
    """The wheel window is only as big as the wheel; repainting a full-screen translucent window
    is too slow (tens of ms per frame on high-res screens) and makes the animation stutter."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.pages = []
        self.page = 0
        self.dim = True
        self.title = ""
        self.theme = theme_from_preset("Dark")
        self.wheel_scale = 1.0
        self.hover = None
        self._set_size(1.0)
        self.center_global = QPointF()
        self.dimmer = DimOverlay()
        self._t = 0.0
        self.is_open = False
        self._tracker = QTimer(self, interval=10, timeout=self._track)
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_anim)
        self._anim.finished.connect(self._on_anim_done)

    def _on_anim(self, v):
        self._t = v
        if self.dim:
            self.dimmer.setWindowOpacity(0.32 * v)
        self.update()

    def _animate(self, to, full_ms, easing):
        """Tween _t from its current value, so quick open/close sequences do not jump."""
        start = self._t
        self._anim.stop()
        # setStartValue/setEndValue on a stopped animation also emit valueChanged,
        # which would overwrite _t before the duration is computed
        self._anim.blockSignals(True)
        self._anim.setStartValue(start)
        self._anim.setEndValue(to)
        self._anim.setEasingCurve(easing)
        self._anim.setDuration(max(1, int(full_ms * abs(to - start))))
        self._anim.blockSignals(False)
        self._anim.start()

    def _on_anim_done(self):
        if not self.is_open:
            # clear the window before hiding it; otherwise Windows briefly shows the stale
            # frame (the full wheel) for a split second when the window appears again
            self._t = 0.0
            self.repaint()
            self.hide()
            self.dimmer.setWindowOpacity(0.0)
            self.dimmer.hide()

    @property
    def slots(self):
        return self.pages[self.page]["slots"] if self.pages else []

    def _set_size(self, size):
        self.wheel_scale = size
        side = int(WHEEL_SIZE * size)
        if self.width() != side:
            self.resize(side, side)
        self.center = QPointF(side / 2, side / 2)

    def open(self, pages, page, position, dim, title, theme):
        self.pages, self.page, self.dim, self.title = pages, min(page, len(pages) - 1), dim, title
        self.theme = theme
        self._set_size(theme.get("size", 1.0))
        half = self.width() // 2
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        geo = screen.geometry()
        if position == "center":
            c = geo.center()
            QCursor.setPos(c)
        else:
            c = cursor
            c.setX(max(geo.left() + half, min(geo.right() - half, c.x())))
            c.setY(max(geo.top() + half, min(geo.bottom() - half, c.y())))
        self.center_global = QPointF(c)
        self.move(c.x() - half, c.y() - half)
        self.hover = None
        self.is_open = True
        self._track()
        if self.dim:
            if self.dimmer.geometry() != geo:
                self.dimmer.setGeometry(geo)
            self.dimmer.show()
        if not self.isVisible():
            self._t = 0.0
            self.show()
        self.raise_()
        self._animate(1.0, 150, QEasingCurve.OutCubic)
        self._tracker.start()

    def close_wheel(self):
        self._tracker.stop()
        if self.is_open:
            self.is_open = False
            self._animate(0.0, 100, QEasingCurve.InQuad)

    def paintEvent(self, e):
        if self._t <= 0.001:
            return  # empty frame (transparent background)
        self._paint()

    def flip(self, step):
        if len(self.pages) > 1:
            self.page = (self.page + step) % len(self.pages)
            self.hover = None
            self._track()
            self.update()

    def _track(self):
        d = QPointF(QCursor.pos()) - self.center_global
        n = len(self.slots)
        hover = None
        if n and math.hypot(d.x(), d.y()) > R_IN * self.wheel_scale:
            step = 360 / n
            ang = math.degrees(math.atan2(d.y(), d.x()))
            hover = int(((ang + 90 + step / 2) % 360) // step)
        if hover != self.hover:
            self.hover = hover
            self.update()

    def _paint(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        t = self._t
        p.setOpacity(t)
        p.translate(self.center)
        s = (0.88 + 0.12 * t) * self.wheel_scale
        p.scale(s, s)
        paint_wheel(p, self.pages, self.page, self.hover, self.title, self.theme)
        p.end()


# ---------------------------------------------------------------- open windows

GW_OWNER, GWL_EXSTYLE = 4, -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
DWMWA_CLOAKED = 14
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

dwmapi = ctypes.WinDLL("dwmapi")
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetForegroundWindow.restype = wt.HWND
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def _process_path(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        return buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


def open_windows():
    """The windows Alt+Tab would list, most recently used first.

    EnumWindows walks the z-order from the top, which is also the order of recent use. The filters
    follow Alt+Tab: visible, unowned top-level windows with a title, no tool windows, and nothing
    cloaked (that skips windows on other virtual desktops and suspended Store apps).
    """
    own_pid = os.getpid()
    found = []

    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) and not ex & WS_EX_APPWINDOW:
            return True
        cloaked = wt.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in SHELL_CLASSES:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        exe = _process_path(pid.value)
        found.append({"name": title.value, "path": exe, "detail": Path(exe).name if exe else "", "hwnd": hwnd})
        return True

    user32.EnumWindows(WNDENUMPROC(check), 0)
    return found


WINDOW_ORDERS = {"recent": "Most recently used first", "name": "Alphabetical by title"}


def window_pages(order="recent"):
    """Pages for an "Open windows" wheel: the open windows, 12 per page."""
    windows = open_windows()
    if order == "name":
        windows.sort(key=lambda w: w["name"].casefold())
    if not windows:
        return [{"name": "Open windows", "slots": [], "empty": "No open windows right now."}]
    chunks = [windows[i:i + MAX_SLOTS] for i in range(0, len(windows), MAX_SLOTS)]
    return [{"name": "Open windows" if i == 0 else f"Open windows {i + 1}", "slots": c} for i, c in enumerate(chunks)]


def activate_window(hwnd):
    """Bring a window to the front, restoring it if minimized. Return False if it no longer exists."""
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):
        # Windows only lets the process that sent the last input change the foreground window;
        # a synthetic key press makes Orbit that process, then the request is allowed
        HotkeyHook._tap_mask_key()
        user32.SetForegroundWindow(hwnd)
    return True


def slot_icon(slot):
    # a window slot uses its program's icon; a slot without a known program gets a generic one
    if not slot["path"]:
        return QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_DesktopIcon).pixmap(40, 40)
    return path_icon(slot["path"])


def _readable_on(c):
    """A text color that is readable on top of color c."""
    return QColor(20, 20, 24) if 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() > 150 else QColor(240, 240, 244)


def paint_wheel(p, pages, page, hover, title, theme):
    """Paint the wheel centered on (0, 0); used by the overlay and the Setting preview."""
    alpha = int(255 * max(0.3, min(1.0, theme.get("opacity", 0.85))))
    slice_col = QColor(theme["slice"])
    slice_col.setAlpha(alpha)
    hi_col = QColor(theme["highlight"])
    hi_col.setAlpha(max(alpha, 235))
    center_col = QColor(theme["center"])
    center_col.setAlpha(min(255, alpha + 20))
    slice_text, hi_text, center_text = (_readable_on(QColor(theme[k])) for k in ("slice", "highlight", "center"))

    slots = pages[page]["slots"] if pages else []
    n = len(slots)
    p.setPen(Qt.NoPen)
    # soft shadow behind the wheel
    p.setBrush(QColor(0, 0, 0, 70))
    p.drawEllipse(QPointF(0, 4), R_OUT + 10, R_OUT + 10)

    if n:
        step = 360 / n
        gap = min(1.4, step * 0.05)
        rm = (R_IN + R_OUT) / 2 + 6
        name_font = QFont("Segoe UI", 9, QFont.DemiBold)
        fm = QFontMetrics(name_font)
        text_w = min(118, 2 * math.pi * rm / n * 0.86)
        for i, slot in enumerate(slots):
            mid = -90 + i * step
            sel = i == hover
            path = ring_slice(R_IN + 4, R_OUT + (8 if sel else 0), mid - step / 2 + gap, mid + step / 2 - gap)
            p.setPen(Qt.NoPen)
            p.setBrush(hi_col if sel else slice_col)
            p.drawPath(path)
            if sel:
                p.setPen(QPen(QColor(theme["highlight"]).lighter(130), 2))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            a = math.radians(mid)
            pos = QPointF(math.cos(a) * rm, math.sin(a) * rm)
            pix = slot_icon(slot)
            p.drawPixmap(QRectF(pos.x() - 18, pos.y() - 30, 36, 36), pix, QRectF(pix.rect()))
            p.setFont(name_font)
            p.setPen(hi_text if sel else slice_text)
            label = fm.elidedText(slot["name"], Qt.ElideRight, int(text_w))
            p.drawText(QRectF(pos.x() - text_w / 2, pos.y() + 8, text_w, 20), Qt.AlignHCenter | Qt.AlignTop, label)
    else:
        p.setBrush(slice_col)
        p.drawPath(ring_slice(R_IN + 4, R_OUT, -269.9, 89.9))

    # center circle
    outline = QColor(center_text)
    outline.setAlpha(40)
    p.setPen(QPen(outline, 1))
    p.setBrush(center_col)
    p.drawEllipse(QPointF(0, 0), R_IN, R_IN)
    box = QRectF(-R_IN + 12, -R_IN + 14, 2 * R_IN - 24, 2 * R_IN - 28)
    if hover is not None:
        slot = slots[hover]
        # an open-window slot shows its program in the center; a folder/file slot shows its path
        text, sub = slot["name"], slot.get("detail") or os.path.expandvars(slot["path"])
        title_col = QColor(center_text)
    else:
        page_name = pages[page]["name"] if pages else ""
        text = title or page_name
        if not n:
            sub = pages[page].get("empty", "Empty page. Add slots in Setting.") if pages else ""
        elif len(pages) > 1 and title:
            sub = page_name  # keep the page name visible while scrolling between pages
        else:
            sub = "Cancel"
        title_col = QColor(center_text)
        title_col.setAlpha(210)
    sub_col = QColor(center_text)
    sub_col.setAlpha(150)
    f1 = QFont("Segoe UI", 11, QFont.Bold)
    p.setFont(f1)
    p.setPen(title_col)
    sub_top = box.top() + 44
    if QFontMetrics(f1).horizontalAdvance(text) <= box.width():
        p.drawText(QRectF(box.left(), box.top() + 18, box.width(), 24), Qt.AlignCenter, text)
    else:
        # long names (window titles especially) get two smaller lines instead of being cut after a few words
        f1 = QFont("Segoe UI", 9, QFont.Bold)
        fm1 = QFontMetrics(f1)
        p.setFont(f1)
        p.drawText(QRectF(box.left(), box.top() + 6, box.width(), fm1.lineSpacing() * 2 + 2),
                   Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap,
                   fm1.elidedText(text, Qt.ElideRight, int(box.width() * 2 - 16)))
        sub_top = box.top() + 50
    f2 = QFont("Segoe UI", 8)
    p.setFont(f2)
    p.setPen(sub_col)
    p.drawText(QRectF(box.left(), sub_top, box.width(), 40), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap,
               QFontMetrics(f2).elidedText(sub, Qt.ElideMiddle, int(box.width() * 2 - 20)))
    # page indicator dots
    if len(pages) > 1:
        k = len(pages)
        x0 = -(k - 1) * 7
        for i in range(k):
            dot = QColor(center_text)
            dot.setAlpha(230 if i == page else 70)
            p.setPen(Qt.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QPointF(x0 + i * 14, R_IN - 18), 3.5, 3.5)


class WheelPreview(QWidget):
    """Wheel preview in Setting; the slot under the cursor lights up like on the real wheel."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 320)
        self.setMouseTracking(True)
        self.pages, self.page, self.hover, self.title = [], 0, 0, ""
        self.theme = theme_from_preset("Dark")

    def set_wheel(self, wheel):
        pages = window_pages(wheel.get("order", "recent")) if wheel.get("type") == "windows" else wheel["pages"]
        self.pages, self.page, self.title, self.theme = pages, 0, wheel.get("title", ""), wheel["theme"]
        self.hover = 0 if self.pages and self.pages[0]["slots"] else None
        self.update()

    def _scale(self):
        # Large must still fit: the base scale is computed for the biggest size
        return min(self.width(), self.height()) / (WHEEL_SIZE * max(SIZES.values())) * self.theme.get("size", 1.0)

    def mouseMoveEvent(self, e):
        d = QPointF(e.position()) - QPointF(self.width() / 2, self.height() / 2)
        n = len(self.pages[self.page]["slots"]) if self.pages else 0
        hover = None
        if n and math.hypot(d.x(), d.y()) > R_IN * self._scale():
            step = 360 / n
            hover = int(((math.degrees(math.atan2(d.y(), d.x())) + 90 + step / 2) % 360) // step)
        if hover != self.hover:
            self.hover = hover
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # desktop-like backdrop so the wheel transparency is visible
        g = QLinearGradient(QPointF(0, 0), QPointF(self.width(), self.height()))
        g.setColorAt(0, QColor(92, 110, 140))
        g.setColorAt(1, QColor(150, 120, 110))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        p.translate(self.width() / 2, self.height() / 2)
        s = self._scale()
        p.scale(s, s)
        paint_wheel(p, self.pages, self.page, self.hover, self.title, self.theme)
        p.end()


# ---------------------------------------------------------------- settings

class HotkeyButton(QPushButton):
    changed = Signal(dict)  # the old hotkey, emitted after a new combination is recorded

    def __init__(self, hook, hk):
        super().__init__()
        self.hook = hook
        self.hk = dict(hk)
        self.recording = False
        self.setMinimumWidth(240)
        self.clicked.connect(self.start)
        self._refresh()

    def _refresh(self):
        self.setText("Press a key combination… (Esc = cancel)" if self.recording else hotkey_label(self.hk))

    def set_hotkey(self, hk):
        self.hk = dict(hk)
        self._refresh()

    def _record(self, hk):
        old = self.hk
        self.hk = hk
        self.stop()
        self.changed.emit(old)

    def start(self):
        if self.recording:
            return
        self.recording = True
        self.hook.paused = True
        self.grabKeyboard()
        self._refresh()

    def stop(self):
        self.recording = False
        self.hook.paused = False
        self.releaseKeyboard()
        self._refresh()

    @staticmethod
    def _mods(qmods):
        mods = []
        if qmods & Qt.ControlModifier:
            mods.append("ctrl")
        if qmods & Qt.AltModifier:
            mods.append("alt")
        if qmods & Qt.ShiftModifier:
            mods.append("shift")
        if qmods & Qt.MetaModifier:
            mods.append("win")
        return mods

    def keyPressEvent(self, e):
        if not self.recording:
            return super().keyPressEvent(e)
        vk = e.nativeVirtualKey()
        mods = self._mods(e.modifiers())
        if vk in MODIFIER_VKS:
            return
        if vk == VK_ESCAPE and not mods:
            self.stop()
            return
        if vk:
            self._record({"mods": mods, "vk": vk})

    def mousePressEvent(self, e):
        if self.recording and e.button() in (Qt.BackButton, Qt.ForwardButton):
            vk = VK_XBUTTON1 if e.button() == Qt.BackButton else VK_XBUTTON2
            self._record({"mods": self._mods(e.modifiers()), "vk": vk})
            return
        super().mousePressEvent(e)

    def focusOutEvent(self, e):
        if self.recording:
            self.stop()
        super().focusOutEvent(e)


class ColorButton(QPushButton):
    changed = Signal(str)  # the new color, "#rrggbb"

    def __init__(self):
        super().__init__()
        self.color = "#000000"
        self.setMinimumWidth(140)
        self.clicked.connect(self._pick)

    def set_color(self, color):
        self.color = color
        fg = _readable_on(QColor(color)).name()
        self.setText(color.upper())
        self.setStyleSheet(f"background: {color}; color: {fg}; border: 1px solid #555; border-radius: 4px; padding: 5px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Choose color")
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(c.name())


class SlotTable(QTableWidget):
    dropped = Signal(list)

    def __init__(self):
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels(["Name", "Folder / file"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasUrls() else super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasUrls() else super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class SettingsDialog(QDialog):
    def __init__(self, cfg, hook, icon):
        super().__init__()
        self.setWindowTitle("Setting")
        self.setWindowIcon(icon)
        self.resize(820, 600)
        self.cfg = copy.deepcopy(cfg)
        self.hook = hook
        self._loading = False

        tabs = QTabWidget()
        tabs.addTab(self._build_wheels_tab(), "Wheels")
        tabs.addTab(self._build_general_tab(), "General")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)

        self._fill_wheel_combo(0)

    # --- hotkeys: every wheel and the Setting hotkey must differ
    def _hotkey_owners(self):
        owners = [(w["name"], w["hotkey"]) for w in self.cfg["wheels"]]
        owners.append(("Setting", self.settings_hotkey_btn.hk))
        return owners

    @staticmethod
    def _same(a, b):
        return int(a["vk"]) == int(b["vk"]) and set(a.get("mods", [])) == set(b.get("mods", []))

    def _conflict(self, hk, skip_name):
        for name, other in self._hotkey_owners():
            if name != skip_name and self._same(hk, other):
                return name
        return None

    def _free_hotkey(self):
        for key in "WERTASDFZXC123456789":
            hk = {"mods": ["alt"], "vk": ord(key)}
            if not self._conflict(hk, None):
                return hk
        return {"mods": ["ctrl", "alt"], "vk": 0x70 + len(self.cfg["wheels"])}  # Ctrl+Alt+F<n>

    def _check_hotkey(self, btn, old, owner):
        other = self._conflict(btn.hk, owner)
        if other:
            QMessageBox.warning(self, "Setting", f"{hotkey_label(btn.hk)} is already used by \"{other}\". "
                                                 "Every wheel and the Setting shortcut need a different hotkey.")
            btn.set_hotkey(old)
            return False
        return True

    # --- tab wheels
    def _build_wheels_tab(self):
        w = QWidget()
        self.wheel_combo = QComboBox()
        self.wheel_combo.currentIndexChanged.connect(self._show_wheel)
        b_add_w = QPushButton("+ Add wheel")
        b_ren_w = QPushButton("Rename")
        self.b_del_w = QPushButton("Delete wheel")
        b_add_w.clicked.connect(self._add_wheel)
        b_ren_w.clicked.connect(self._rename_wheel)
        self.b_del_w.clicked.connect(self._del_wheel)
        top = QHBoxLayout()
        top.addWidget(QLabel("Wheel:"))
        top.addWidget(self.wheel_combo, 1)
        for b in (b_add_w, b_ren_w, self.b_del_w):
            top.addWidget(b)

        self.wheel_hotkey_btn = HotkeyButton(self.hook, self.cfg["wheels"][0]["hotkey"])
        self.wheel_hotkey_btn.changed.connect(self._wheel_hotkey_changed)
        self.wheel_title = QLineEdit()
        self.wheel_title.setPlaceholderText("Leave empty to show the page name")
        self.wheel_title.textEdited.connect(self._title_edited)
        form = QFormLayout()
        form.addRow("Hotkey (hold):", self.wheel_hotkey_btn)
        form.addRow("Center text:", self.wheel_title)
        self.wheel_type = QComboBox()
        for kind, label in WHEEL_TYPES.items():
            self.wheel_type.addItem(label, kind)
        self.wheel_type.activated.connect(self._type_changed)
        form.addRow("Type:", self.wheel_type)
        self.type_hint = QLabel("This wheel fills itself with the windows that are open when you press its hotkey. "
                                "Pick one to bring it to the front.")
        self.type_hint.setWordWrap(True)
        form.addRow("", self.type_hint)
        self.window_order = QComboBox()
        for order, label in WINDOW_ORDERS.items():
            self.window_order.addItem(label, order)
        self.window_order.activated.connect(self._order_changed)
        self.window_order_label = QLabel("Order:")
        form.addRow(self.window_order_label, self.window_order)

        self.page_list = QListWidget()
        self.page_list.setMaximumWidth(180)
        self.page_list.currentRowChanged.connect(self._show_page)
        b_add_pg = QPushButton("+ Page")
        b_ren_pg = QPushButton("Rename page")
        b_del_pg = QPushButton("Delete page")
        b_add_pg.clicked.connect(self._add_page)
        b_ren_pg.clicked.connect(self._rename_page)
        b_del_pg.clicked.connect(self._del_page)
        left = QVBoxLayout()
        left.addWidget(QLabel("Pages (scroll while the wheel is open)"))
        left.addWidget(self.page_list)
        for b in (b_add_pg, b_ren_pg, b_del_pg):
            left.addWidget(b)

        self.table = SlotTable()
        self.table.itemChanged.connect(self._table_edited)
        self.table.dropped.connect(self._add_paths)
        self.count_label = QLabel()

        self.b_add_dir = QPushButton("+ Folder…")
        self.b_add_file = QPushButton("+ File…")
        b_target = QPushButton("Change target…")
        b_del = QPushButton("Remove slot")
        b_up = QPushButton("▲")
        b_down = QPushButton("▼")
        self.b_add_dir.clicked.connect(lambda: self._pick(folder=True))
        self.b_add_file.clicked.connect(lambda: self._pick(folder=False))
        target_menu = QMenu(self)
        target_menu.addAction("Choose folder…", lambda: self._pick(folder=True, replace=True))
        target_menu.addAction("Choose file…", lambda: self._pick(folder=False, replace=True))
        b_target.setMenu(target_menu)
        b_del.clicked.connect(self._del_slot)
        b_up.clicked.connect(lambda: self._move(-1))
        b_down.clicked.connect(lambda: self._move(1))
        row = QHBoxLayout()
        for b in (self.b_add_dir, self.b_add_file, b_target, b_del):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(b_up)
        row.addWidget(b_down)

        right = QVBoxLayout()
        hint = QLabel("Slots go clockwise, starting from the top. Double-click a name to rename it. "
                      "You can also drag folders/files from Explorer onto the table.")
        hint.setWordWrap(True)
        right.addWidget(hint)
        right.addWidget(self.table)
        right.addLayout(row)
        right.addWidget(self.count_label)

        slots_tab = QWidget()
        body = QHBoxLayout(slots_tab)
        body.addLayout(left)
        body.addLayout(right, 1)
        inner = self.inner_tabs = QTabWidget()
        inner.addTab(slots_tab, "Slots")
        inner.addTab(self._build_appearance(), "Appearance")

        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addLayout(form)
        lay.addWidget(inner, 1)
        return w

    # --- appearance of the selected wheel
    def _build_appearance(self):
        w = QWidget()
        form = QFormLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES) + ["Custom"])
        self.theme_combo.activated.connect(self._preset_chosen)
        form.addRow("Theme:", self.theme_combo)
        self.color_btns = {}
        for key, label in (("slice", "Slice color:"), ("highlight", "Highlight color:"), ("center", "Center color:")):
            btn = ColorButton()
            btn.changed.connect(lambda c, k=key: self._color_changed(k, c))
            self.color_btns[key] = btn
            form.addRow(label, btn)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(40, 100)
        self.opacity_label = QLabel()
        self.opacity.valueChanged.connect(self._opacity_changed)
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity, 1)
        op_row.addWidget(self.opacity_label)
        form.addRow("Opacity:", op_row)
        self.size_combo = QComboBox()
        for name, value in SIZES.items():
            self.size_combo.addItem(name, value)
        self.size_combo.activated.connect(self._size_changed)
        form.addRow("Size:", self.size_combo)
        hint = QLabel("Move the mouse over the preview to see the highlight.")
        hint.setWordWrap(True)
        form.addRow("", hint)

        self.preview = WheelPreview()
        lay = QHBoxLayout(w)
        lay.addLayout(form, 1)
        lay.addWidget(self.preview, 1)
        return w

    def _theme(self):
        return self._wheel()["theme"]

    def _show_appearance(self):
        t = self._theme()
        self.theme_combo.setCurrentText(t.get("preset", "Custom"))
        for key, btn in self.color_btns.items():
            btn.set_color(t[key])
        self.opacity.blockSignals(True)
        self.opacity.setValue(round(t.get("opacity", 0.85) * 100))
        self.opacity.blockSignals(False)
        self.opacity_label.setText(f"{self.opacity.value()}%")
        i = self.size_combo.findData(t.get("size", 1.0))
        self.size_combo.setCurrentIndex(i if i >= 0 else 1)
        self.preview.set_wheel(self._wheel())

    def _preset_chosen(self, _i):
        name = self.theme_combo.currentText()
        if name in THEMES:
            self._wheel()["theme"] = theme_from_preset(name, self._theme())
            self._show_appearance()

    def _color_changed(self, key, color):
        t = self._theme()
        t[key] = color
        t["preset"] = "Custom"
        self.theme_combo.setCurrentText("Custom")
        self.preview.update()

    def _opacity_changed(self, v):
        self._theme()["opacity"] = v / 100
        self.opacity_label.setText(f"{v}%")
        self.preview.update()

    def _size_changed(self, _i):
        self._theme()["size"] = self.size_combo.currentData()
        self.preview.update()

    def _title_edited(self, text):
        self._wheel()["title"] = text.strip()
        self.preview.title = text.strip()
        self.preview.update()

    def _wheel(self):
        return self.cfg["wheels"][max(0, self.wheel_combo.currentIndex())]

    @staticmethod
    def _wheel_label(w):
        return f"{w['name']}   ({hotkey_label(w['hotkey'])})"

    def _fill_wheel_combo(self, select):
        self.wheel_combo.blockSignals(True)
        self.wheel_combo.clear()
        for w in self.cfg["wheels"]:
            self.wheel_combo.addItem(self._wheel_label(w))
        self.wheel_combo.setCurrentIndex(select)
        self.wheel_combo.blockSignals(False)
        self._show_wheel()

    def _show_wheel(self, _index=None):
        w = self._wheel()
        self.wheel_hotkey_btn.set_hotkey(w["hotkey"])
        self.wheel_title.setText(w.get("title", ""))
        self.b_del_w.setEnabled(len(self.cfg["wheels"]) > 1)
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for pg in w["pages"]:
            self.page_list.addItem(pg["name"])
        self.page_list.blockSignals(False)
        self.page_list.setCurrentRow(0)
        self._show_page()
        self._show_type()

    def _show_type(self):
        # an "Open windows" wheel has no slots to set up, so only its Appearance tab applies
        windows = self._wheel().get("type") == "windows"
        self.wheel_type.setCurrentIndex(self.wheel_type.findData(self._wheel().get("type", "folders")))
        self.type_hint.setVisible(windows)
        self.window_order.setVisible(windows)
        self.window_order_label.setVisible(windows)
        self.window_order.setCurrentIndex(self.window_order.findData(self._wheel().get("order", "recent")))
        self.inner_tabs.setTabEnabled(0, not windows)
        if windows:
            self.inner_tabs.setCurrentIndex(1)
        self._show_appearance()

    def _type_changed(self, _i):
        self._wheel()["type"] = self.wheel_type.currentData()
        self._show_type()

    def _order_changed(self, _i):
        self._wheel()["order"] = self.window_order.currentData()
        self._show_appearance()  # the preview lists the open windows in the chosen order

    def _wheel_hotkey_changed(self, old):
        if self._check_hotkey(self.wheel_hotkey_btn, old, self._wheel()["name"]):
            self._wheel()["hotkey"] = self.wheel_hotkey_btn.hk
            self.wheel_combo.setItemText(self.wheel_combo.currentIndex(), self._wheel_label(self._wheel()))

    def _add_wheel(self):
        n = len(self.cfg["wheels"]) + 1
        name, ok = QInputDialog.getText(self, "New wheel", "Wheel name:", text=f"Wheel {n}")
        if not (ok and name.strip()):
            return
        name = name.strip()
        if any(w["name"] == name for w in self.cfg["wheels"]):
            QMessageBox.information(self, "Setting", f"There is already a wheel named \"{name}\".")
            return
        self.cfg["wheels"].append(new_wheel(name, self._free_hotkey(), name))
        self._fill_wheel_combo(len(self.cfg["wheels"]) - 1)

    def _rename_wheel(self):
        w = self._wheel()
        name, ok = QInputDialog.getText(self, "Rename wheel", "Wheel name:", text=w["name"])
        name = name.strip()
        if not (ok and name) or name == w["name"]:
            return
        if any(o["name"] == name for o in self.cfg["wheels"]):
            QMessageBox.information(self, "Setting", f"There is already a wheel named \"{name}\".")
            return
        w["name"] = name
        self.wheel_combo.setItemText(self.wheel_combo.currentIndex(), self._wheel_label(w))

    def _del_wheel(self):
        i = self.wheel_combo.currentIndex()
        if len(self.cfg["wheels"]) <= 1:
            return
        if QMessageBox.question(self, "Delete wheel",
                                f"Delete wheel \"{self.cfg['wheels'][i]['name']}\" and all its slots?") == QMessageBox.Yes:
            del self.cfg["wheels"][i]
            self._fill_wheel_combo(max(0, i - 1))

    # --- pages and slots (of the selected wheel)
    def _page(self):
        r = self.page_list.currentRow()
        pages = self._wheel()["pages"]
        return pages[r] if 0 <= r < len(pages) else None

    def _show_page(self, _row=None, select=None):
        pg = self._page()
        self._loading = True
        self.table.setRowCount(0)
        if pg:
            for s in pg["slots"]:
                r = self.table.rowCount()
                self.table.insertRow(r)
                name = QTableWidgetItem(s["name"])
                name.setIcon(QIcon(path_icon(s["path"])))
                self.table.setItem(r, 0, name)
                self.table.setItem(r, 1, QTableWidgetItem(s["path"]))
        self._loading = False
        n = len(pg["slots"]) if pg else 0
        self.count_label.setText(f"{n} / {MAX_SLOTS} slots on this page")
        if self._wheel().get("type") != "windows":  # an "Open windows" preview shows live windows instead
            self.preview.page = max(0, self.page_list.currentRow())
            self.preview.hover = None if not n or (self.preview.hover or 0) >= n else self.preview.hover
            self.preview.update()
        self.b_add_dir.setEnabled(n < MAX_SLOTS)
        self.b_add_file.setEnabled(n < MAX_SLOTS)
        if select is not None and 0 <= select < n:
            self.table.selectRow(select)

    def _table_edited(self, item):
        if self._loading:
            return
        slot = self._page()["slots"][item.row()]
        key = "name" if item.column() == 0 else "path"
        slot[key] = item.text().strip()
        if key == "path":
            slot["name"] = Path(slot["path"]).name or slot["path"]
            _icon_cache.pop(slot["path"], None)
            self._show_page(select=item.row())

    def _add_paths(self, paths, replace_row=None):
        pg = self._page()
        if not pg:
            return
        last = None
        for path in paths:
            path = os.path.normpath(path)
            entry = {"name": Path(path).name or path, "path": path}
            if replace_row is not None:
                pg["slots"][replace_row].update(entry)  # the name follows the new folder/file
                last = replace_row
                break
            if len(pg["slots"]) >= MAX_SLOTS:
                QMessageBox.information(self, "Setting", f"A page holds at most {MAX_SLOTS} slots. "
                                                         "Add another page for more.")
                break
            pg["slots"].append(entry)
            last = len(pg["slots"]) - 1
        self._show_page(select=last)

    def _pick(self, folder, replace=False):
        row = self.table.currentRow()
        if replace and row < 0:
            QMessageBox.information(self, "Setting", "Select the slot you want to change first.")
            return
        if folder:
            path = QFileDialog.getExistingDirectory(self, "Choose folder", str(Path.home()))
        else:
            # keep shortcuts as .lnk: resolving them to the target exe would drop their "Start in" folder
            path, _ = QFileDialog.getOpenFileName(self, "Choose file", str(Path.home()),
                                                  options=QFileDialog.DontResolveSymlinks)
        if path:
            self._add_paths([path], replace_row=row if replace else None)

    def _del_slot(self):
        row = self.table.currentRow()
        if row >= 0:
            del self._page()["slots"][row]
            self._show_page(select=min(row, len(self._page()["slots"]) - 1))

    def _move(self, d):
        row = self.table.currentRow()
        slots = self._page()["slots"] if self._page() else []
        if 0 <= row < len(slots) and 0 <= row + d < len(slots):
            slots[row], slots[row + d] = slots[row + d], slots[row]
            self._show_page(select=row + d)

    def _add_page(self):
        pages = self._wheel()["pages"]
        name, ok = QInputDialog.getText(self, "New page", "Page name:", text=f"Page {len(pages) + 1}")
        if ok and name.strip():
            pages.append({"name": name.strip(), "slots": []})
            self.page_list.addItem(name.strip())
            self.page_list.setCurrentRow(self.page_list.count() - 1)

    def _rename_page(self):
        pg = self._page()
        if pg:
            name, ok = QInputDialog.getText(self, "Rename page", "Page name:", text=pg["name"])
            if ok and name.strip():
                pg["name"] = name.strip()
                self.page_list.currentItem().setText(pg["name"])

    def _del_page(self):
        pages = self._wheel()["pages"]
        r = self.page_list.currentRow()
        if len(pages) <= 1:
            QMessageBox.information(self, "Setting", "A wheel needs at least one page.")
            return
        if QMessageBox.question(self, "Delete page", f"Delete page \"{pages[r]['name']}\" and its slots?") == QMessageBox.Yes:
            del pages[r]
            self.page_list.takeItem(r)

    # --- tab general
    def _build_general_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.settings_hotkey_btn = HotkeyButton(self.hook, self.cfg["settings_hotkey"])
        self.settings_hotkey_btn.changed.connect(
            lambda old: self._check_hotkey(self.settings_hotkey_btn, old, "Setting"))
        form.addRow("Open Setting (press):", self.settings_hotkey_btn)
        hk_hint = QLabel("Click a hotkey button, then press the new combination, e.g. Alt+Q, Ctrl+Shift+Space "
                         "or a side mouse button. Each wheel's hotkey is set on the Wheels tab.")
        hk_hint.setWordWrap(True)
        form.addRow("", hk_hint)
        self.position = QComboBox()
        self.position.addItem("At the mouse cursor", "cursor")
        self.position.addItem("At the center of the screen", "center")
        self.position.setCurrentIndex(0 if self.cfg.get("position") != "center" else 1)
        form.addRow("Wheel position:", self.position)
        self.folder_open = QComboBox()
        self.folder_open.addItem("A new Explorer window", "window")
        self.folder_open.addItem("A new tab in the open Explorer window", "tab")
        self.folder_open.setCurrentIndex(1 if self.cfg.get("folder_open", "window") == "tab" else 0)
        form.addRow("Open folders in:", self.folder_open)
        self.open_page = QComboBox()
        self.open_page.addItem("Always the first page", "first")
        self.open_page.addItem("The page I used last", "last")
        self.open_page.setCurrentIndex(1 if self.cfg.get("open_page", "first") == "last" else 0)
        form.addRow("Wheel opens on:", self.open_page)
        tab_hint = QLabel("Tabs need Windows 11. With no Explorer window open, or on Windows 10, "
                          "folders open in a new window.")
        tab_hint.setWordWrap(True)
        form.addRow("", tab_hint)
        self.dim = QCheckBox("Dim the screen slightly while a wheel is open")
        self.dim.setChecked(self.cfg.get("dim", True))
        form.addRow("", self.dim)
        self.autostart = QCheckBox("Start automatically when I sign in to Windows")
        self.autostart.setChecked(get_autostart())
        form.addRow("", self.autostart)
        loc = QLabel(f"Config: {CONFIG_PATH}")
        loc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("", loc)
        return w

    def accept(self):
        owners = self._hotkey_owners()
        for i, (a_name, a) in enumerate(owners):
            for b_name, b in owners[i + 1:]:
                if self._same(a, b):
                    QMessageBox.warning(self, "Setting", f"\"{a_name}\" and \"{b_name}\" both use {hotkey_label(a)}. "
                                                         "Every hotkey must be different.")
                    return
        super().accept()

    def result_config(self):
        cfg = self.cfg
        cfg["settings_hotkey"] = self.settings_hotkey_btn.hk
        cfg["position"] = self.position.currentData()
        cfg["dim"] = self.dim.isChecked()
        cfg["folder_open"] = self.folder_open.currentData()
        cfg["open_page"] = self.open_page.currentData()
        return cfg


# ---------------------------------------------------------------- app

def make_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(32, 32)
    p.setPen(Qt.NoPen)
    for i in range(6):
        mid = -90 + i * 60
        p.setBrush(QColor(245, 245, 245) if i == 0 else QColor(70, 70, 80))
        p.drawPath(ring_slice(11, 30, mid - 26, mid + 26))
    p.setBrush(QColor(245, 245, 245))
    p.drawEllipse(QPointF(0, 0), 6, 6)
    p.end()
    return QIcon(pix)


EXPLORER_NEW_TAB = 0xA21B  # WM_COMMAND id behind Explorer's "New tab" (Ctrl+T) on Windows 11


def _frontmost_explorer():
    """Handle of the topmost visible File Explorer window, or None."""
    found = []

    def check(h, _):
        if not found and win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "CabinetWClass":
            found.append(h)
        return True

    win32gui.EnumWindows(check, None)  # enumerates in z-order, topmost first
    return found[0] if found else None


def _explorer_tabs(hwnd):
    """Shell window objects (one per tab) belonging to Explorer window hwnd, oldest first."""
    tabs = []
    for w in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            if w.HWND == hwnd:
                tabs.append(w)
        except Exception:
            pass
    return tabs


class OrbitApp(QObject):
    def __init__(self, status, cfg, startup=False):
        super().__init__()
        self.cfg = cfg
        self.fallback = status == "fallback"
        self.page_of = {}         # last page per wheel
        self.active_wheel = 0
        self.icon = make_icon()
        self.settings = None

        self.hook = HotkeyHook()
        self.hook.settings_requested.connect(self.open_settings)
        self.overlay = WheelOverlay()
        self.hook.pressed.connect(self.show_wheel)
        self.hook.released.connect(self.choose)
        self.hook.clicked.connect(self.choose)
        self.hook.cancel.connect(self.hide_wheel)
        self.hook.scrolled.connect(self.overlay.flip)

        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        self.settings_action = menu.addAction("Setting…", self.open_settings)
        menu.addAction("Open config folder", lambda: os.startfile(CONFIG_DIR))
        menu.addSeparator()
        menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.open_settings() if r == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()
        self.apply_config(cfg)

        if status == "new" and not startup:
            QTimer.singleShot(300, self.open_settings)
        elif not self.fallback:
            self.tray.showMessage("Orbit is running", self._hotkey_summary(), self.icon, 3000)
        # the existing config could not be read yet: keep retrying in the background
        self._reload_timer = QTimer(self, interval=3000, timeout=self._retry_load)
        if self.fallback:
            self._reload_timer.start()

    def _hotkey_summary(self):
        return "\n".join(f"Hold {hotkey_label(w['hotkey'])}: {w['name']}" for w in self.cfg["wheels"])

    def _retry_load(self):
        try:
            cfg = _read_config(CONFIG_PATH)
        except (OSError, ValueError) as e:
            log(f"config reload failed: {e!r}")
            return
        log(f"config reloaded, {slot_count(cfg)} slots")
        self._reload_timer.stop()
        self.fallback = False
        self.apply_config(cfg)

    def apply_config(self, cfg):
        self.cfg = cfg
        self.page_of = {}
        self.hook.set_wheel_hotkeys([w["hotkey"] for w in cfg["wheels"]])
        self.hook.set_settings_hotkey(cfg["settings_hotkey"])
        self.tray.setToolTip("Orbit\n" + self._hotkey_summary())
        self.settings_action.setText(f"Setting…  ({hotkey_label(cfg['settings_hotkey'])})")
        self._warm_up()

    def _warm_up(self):
        """Prepare icons and the overlay window early so the first wheel does not stutter."""
        for w in self.cfg["wheels"]:
            for pg in w["pages"]:
                for s in pg["slots"]:
                    path_icon(s["path"])
        ov = self.overlay
        if not ov.is_open and not ov.isVisible():
            # paint the wheel once off-screen so fonts and icons are ready
            dpr = QGuiApplication.primaryScreen().devicePixelRatio()
            pix = QPixmap(ov.size() * dpr)
            pix.setDevicePixelRatio(dpr)
            first = self.cfg["wheels"][0]
            ov.pages, ov.page, ov.title, ov.theme, ov._t = first["pages"], 0, first.get("title", ""), first["theme"], 1.0
            for hover in (None, 0):
                ov.hover = hover
                pix.fill(Qt.transparent)
                ov.render(pix)
            ov._t, ov.hover = 0.0, None
            ov.dimmer.setGeometry(QGuiApplication.primaryScreen().geometry())
            ov.dimmer.show()  # opacity 0 -> invisible
            ov.show()  # _t = 0 -> empty frame
            QTimer.singleShot(50, lambda: ov.is_open or (ov.hide(), ov.dimmer.hide()))

    def show_wheel(self, index):
        if self.overlay.is_open or index >= len(self.cfg["wheels"]):
            return
        w = self.cfg["wheels"][index]
        self.active_wheel = index
        self.hook.wheel_open = True
        if w.get("type") == "windows":
            # read fresh every time, so the wheel matches what is open now
            pages, page = window_pages(w.get("order", "recent")), 0
        else:
            resume = self.cfg.get("open_page", "first") == "last"
            pages, page = w["pages"], self.page_of.get(index, 0) if resume else 0
        self.overlay.open(pages, page, self.cfg.get("position", "cursor"),
                          self.cfg.get("dim", True), w.get("title", ""), w["theme"])

    def hide_wheel(self):
        self.hook.wheel_open = False
        self.overlay.close_wheel()

    def choose(self):
        if not self.overlay.is_open:
            return
        self.overlay._track()
        idx = self.overlay.hover
        self.page_of[self.active_wheel] = self.overlay.page
        slot = self.overlay.slots[idx] if idx is not None else None
        self.hide_wheel()
        if slot:
            self.launch(slot)

    def launch(self, slot):
        if "hwnd" in slot:  # a slot of an "Open windows" wheel
            if not activate_window(slot["hwnd"]):
                self.tray.showMessage("Orbit", f"This window is no longer open:\n{slot['name']}",
                                      QSystemTrayIcon.Warning, 3000)
            return
        path = os.path.expandvars(slot["path"])
        if not os.path.exists(path):
            self.tray.showMessage("Orbit", f"Not found:\n{path}", QSystemTrayIcon.Warning, 4000)
            return
        user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY: let Explorer come to the front
        if os.path.isdir(path) and self.cfg.get("folder_open", "window") == "tab" and self._open_in_new_tab(path):
            return
        self._shell_open(path)

    def _shell_open(self, path):
        # Start programs in their own folder, like a double-click in Explorer does. Without this they inherit
        # Orbit's working folder, and apps that load files by relative path (OBS: locale/en-US.ini) fail.
        # Shortcuts (.lnk) and folders get no folder, so a shortcut's own "Start in" still applies.
        workdir = None
        if os.path.isfile(path) and not path.lower().endswith(".lnk"):
            workdir = os.path.dirname(path)
        result = shell32.ShellExecuteW(None, "open", path, None, workdir, 1)  # SW_SHOWNORMAL
        if result <= 32:  # ShellExecute reports errors as values up to 32
            self.tray.showMessage("Orbit", f"Could not open {path}\n(error {result})", QSystemTrayIcon.Warning, 4000)

    def _open_in_new_tab(self, path):
        """Open a folder as a new tab of the frontmost File Explorer window (Windows 11).

        Windows has no API for this, so Orbit asks the window to add a tab (the same command as Ctrl+T)
        and then points the new tab at the folder. Returns False when there is no window with tabs,
        and the caller opens a new window instead.
        """
        if win32gui is None:
            return False
        try:
            hwnd = _frontmost_explorer()
            tab_host = win32gui.FindWindowEx(hwnd, 0, "ShellTabWindowClass", None) if hwnd else 0
            if not tab_host:
                return False
            before = len(_explorer_tabs(hwnd))
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            win32gui.PostMessage(tab_host, 0x0111, EXPLORER_NEW_TAB, 0)  # WM_COMMAND
        except Exception as e:  # COM/window errors: just use a new window
            log(f"new tab failed: {e!r}")
            return False
        self._finish_new_tab(hwnd, before, path, 0)
        return True

    def _finish_new_tab(self, hwnd, before, path, tries):
        # the tab appears asynchronously; poll briefly without blocking the wheel's closing animation
        try:
            tabs = _explorer_tabs(hwnd)
            if len(tabs) > before:
                tabs[-1].Navigate2(path)  # the newest tab is listed last
                user32.SetForegroundWindow(hwnd)
                return
        except Exception as e:
            log(f"new tab failed: {e!r}")
        if tries < 40:
            QTimer.singleShot(50, lambda: self._finish_new_tab(hwnd, before, path, tries + 1))
        else:
            log("new tab did not appear; opening a new window")
            self._shell_open(path)

    def open_settings(self):
        if self.settings is not None:
            self._bring_settings_front()
            return
        if self.fallback:
            self._retry_load()
        if self.fallback:
            # never let default settings be saved over the user's config
            QMessageBox.warning(None, "Setting",
                                f"Your saved settings can't be read yet:\n{CONFIG_PATH}\n\n"
                                "Orbit keeps retrying in the background. Try opening Setting again in a moment.")
            return
        self.settings = SettingsDialog(self.cfg, self.hook, self.icon)
        QTimer.singleShot(0, self._bring_settings_front)
        if self.settings.exec() == QDialog.Accepted:
            cfg = self.settings.result_config()
            try:
                set_autostart(self.settings.autostart.isChecked())
            except (OSError, subprocess.CalledProcessError) as e:
                QMessageBox.warning(None, "Setting", f"Could not change the startup setting:\n{e}")
            try:
                save_config(cfg)
            except OSError as e:
                log(f"save_config failed: {e!r}")
                QMessageBox.warning(None, "Setting", f"Could not save the settings:\n{e}")
            self.apply_config(cfg)
        self.settings = None

    def _bring_settings_front(self):
        if self.settings is not None:
            self.settings.raise_()
            self.settings.activateWindow()
            user32.SetForegroundWindow(int(self.settings.winId()))


def main():
    log(f"start: argv={sys.argv} cwd={os.getcwd()}")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(make_icon())

    # single instance: if Orbit is already running, ask that instance to open Setting
    server_name = f"{APP_NAME}-{os.environ.get('USERNAME', 'user')}"
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(300):
        if STARTUP_ARG not in sys.argv:
            sock.write(b"settings")
            sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    startup = STARTUP_ARG in sys.argv
    migrate_autostart()
    if not startup and get_autostart():
        # refresh an old shortcut (without --startup) and follow this file if it moved
        try:
            set_autostart(True)
        except (OSError, subprocess.CalledProcessError) as e:
            log(f"updating the autostart shortcut failed: {e!r}")
    cfg, status = load_config(startup=startup)
    fw = OrbitApp(status, cfg, startup)

    def on_conn():
        c = server.nextPendingConnection()
        c.disconnected.connect(c.deleteLater)
        if c.waitForReadyRead(300) and bytes(c.readAll()).startswith(b"settings"):
            QTimer.singleShot(0, fw.open_settings)

    server.newConnection.connect(on_conn)
    if "--settings" in sys.argv:
        QTimer.singleShot(300, fw.open_settings)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

"""Buat assets/orbit.ico dari ikon tray yang digambar di orbit.pyw."""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QSize
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
src = ROOT / "orbit.pyw"
ns = {"__name__": "orbit", "__file__": str(src)}
exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), ns)

app = QApplication(sys.argv)
icon = ns["make_icon"]()
buf = QBuffer()
buf.open(QBuffer.ReadWrite)
icon.pixmap(QSize(256, 256)).save(buf, "PNG")
img = Image.open(io.BytesIO(bytes(buf.data())))
out = ROOT / "assets" / "orbit.ico"
out.parent.mkdir(exist_ok=True)
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)

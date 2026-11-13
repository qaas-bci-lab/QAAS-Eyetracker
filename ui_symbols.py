"""Small vector marks shared by the desktop interface.

Qt painting supplies interface icons; the brand mark loads the project's PNG.
Importing this module does not create an application or access a camera. Call
the rendering functions after the host has created its QApplication.
"""
from functools import lru_cache
import math

from app_paths import RESOURCE_ROOT
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap


ICON_NAMES = (
    'eye', 'play', 'chart', 'settings', 'folder', 'download', 'check', 'plus',
    'copy', 'trash', 'image', 'target', 'back', 'close', 'camera', 'file', 'pause',
)


def _check_gui():
    if QGuiApplication.instance() is None:
        raise RuntimeError('Create QApplication before rendering interface symbols.')


def _size(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 8 <= value <= 512:
        raise ValueError('Symbol size must be an integer between 8 and 512.')
    return value


def _pen(color, width=1.7):
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _line(painter, x1, y1, x2, y2):
    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _eye(path):
    path.moveTo(2.8, 12)
    path.cubicTo(7.2, 4.5, 16.8, 4.5, 21.2, 12)
    path.cubicTo(16.8, 19.5, 7.2, 19.5, 2.8, 12)
    path.closeSubpath()


def _draw(painter, name):
    path = QPainterPath()
    if name == 'eye':
        _eye(path)
        painter.drawEllipse(QPointF(12, 12), 2.7, 2.7)
    elif name == 'play':
        path.moveTo(8, 4.7)
        path.lineTo(19, 12)
        path.lineTo(8, 19.3)
        path.closeSubpath()
    elif name == 'pause':
        painter.drawRoundedRect(QRectF(6, 4.5, 3.5, 15), 1, 1)
        painter.drawRoundedRect(QRectF(14.5, 4.5, 3.5, 15), 1, 1)
    elif name == 'chart':
        path.moveTo(4, 4)
        path.lineTo(4, 20)
        path.lineTo(21, 20)
        path.moveTo(7, 14)
        path.lineTo(11, 10)
        path.lineTo(15, 12)
        path.lineTo(20, 6)
    elif name == 'settings':
        # Three simple slider rows stay legible at toolbar sizes.
        for y, x in ((6, 9), (12, 16), (18, 7)):
            _line(painter, 3.5, y, x - 2.5, y)
            _line(painter, x + 2.5, y, 20.5, y)
            painter.drawEllipse(QPointF(x, y), 2.3, 2.3)
    elif name == 'folder':
        path.moveTo(3, 7)
        path.lineTo(3, 5)
        path.quadTo(3, 4, 4, 4)
        path.lineTo(9, 4)
        path.lineTo(11.5, 7)
        path.lineTo(20, 7)
        path.quadTo(21, 7, 21, 8)
        path.lineTo(21, 18)
        path.quadTo(21, 20, 19, 20)
        path.lineTo(5, 20)
        path.quadTo(3, 20, 3, 18)
        path.lineTo(3, 7)
    elif name == 'download':
        path.moveTo(12, 3)
        path.lineTo(12, 15)
        path.moveTo(7.5, 10.5)
        path.lineTo(12, 15)
        path.lineTo(16.5, 10.5)
        path.moveTo(4, 16)
        path.lineTo(4, 20)
        path.lineTo(20, 20)
        path.lineTo(20, 16)
    elif name == 'check':
        path.moveTo(4.5, 12)
        path.lineTo(9.5, 17)
        path.lineTo(19.5, 6.5)
    elif name == 'plus':
        _line(painter, 12, 4, 12, 20)
        _line(painter, 4, 12, 20, 12)
    elif name == 'copy':
        painter.drawRoundedRect(QRectF(8, 8, 12, 13), 2, 2)
        path.moveTo(16, 5)
        path.lineTo(16, 3)
        path.lineTo(4, 3)
        path.lineTo(4, 16)
        path.lineTo(5, 16)
    elif name == 'trash':
        path.moveTo(3.5, 6)
        path.lineTo(20.5, 6)
        path.moveTo(8, 6)
        path.lineTo(8, 3)
        path.lineTo(16, 3)
        path.lineTo(16, 6)
        path.moveTo(5.5, 6)
        path.lineTo(6.5, 20.5)
        path.lineTo(17.5, 20.5)
        path.lineTo(18.5, 6)
        path.moveTo(10, 10)
        path.lineTo(10.5, 17)
        path.moveTo(14, 10)
        path.lineTo(13.5, 17)
    elif name == 'image':
        painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
        painter.drawEllipse(QPointF(8, 9), 1.5, 1.5)
        path.moveTo(4, 18)
        path.lineTo(10, 12)
        path.lineTo(14, 16)
        path.lineTo(17, 13)
        path.lineTo(21, 17)
    elif name == 'target':
        painter.drawEllipse(QPointF(12, 12), 6.5, 6.5)
        painter.drawEllipse(QPointF(12, 12), 1.8, 1.8)
        for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
            a, b = 9, 11
            _line(painter, 12 + a * math.cos(angle), 12 + a * math.sin(angle),
                  12 + b * math.cos(angle), 12 + b * math.sin(angle))
    elif name == 'back':
        path.moveTo(10, 5)
        path.lineTo(3.5, 12)
        path.lineTo(10, 19)
        path.moveTo(4, 12)
        path.lineTo(20.5, 12)
    elif name == 'close':
        _line(painter, 5, 5, 19, 19)
        _line(painter, 19, 5, 5, 19)
    elif name == 'camera':
        path.moveTo(3, 7)
        path.lineTo(7.5, 7)
        path.lineTo(9, 4)
        path.lineTo(15, 4)
        path.lineTo(16.5, 7)
        path.lineTo(21, 7)
        path.lineTo(21, 20)
        path.lineTo(3, 20)
        path.closeSubpath()
        painter.drawEllipse(QPointF(12, 13), 3.7, 3.7)
    elif name == 'file':
        path.moveTo(5, 3)
        path.lineTo(14, 3)
        path.lineTo(19, 8)
        path.lineTo(19, 21)
        path.lineTo(5, 21)
        path.closeSubpath()
        path.moveTo(14, 3)
        path.lineTo(14, 8)
        path.lineTo(19, 8)
        path.moveTo(8, 12)
        path.lineTo(16, 12)
        path.moveTo(8, 16)
        path.lineTo(14, 16)
    painter.drawPath(path)


@lru_cache(maxsize=192)
def _icon(name, color, size):
    result = QIcon()
    for ratio in (1, 2):
        pixels = size * ratio
        pixmap = QPixmap(pixels, pixels)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(pixels / 24, pixels / 24)
        painter.setPen(_pen(color))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _draw(painter, name)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        result.addPixmap(pixmap)
    return result


def icon(name, color='#555555', size=24):
    """Return a round-ended, outline icon with 1x and 2x raster variants."""
    _check_gui()
    size = _size(size)
    if name not in ICON_NAMES:
        raise ValueError(f'Unknown interface icon: {name}')
    value = QColor(color)
    if not value.isValid():
        raise ValueError('Invalid interface icon color.')
    return QIcon(_icon(name, value.name(QColor.NameFormat.HexArgb), size))


@lru_cache(maxsize=12)
def _brand_pixmap(size, ratio, logo_path, logo_stamp):
    pixels = max(1, round(size * ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    supplied = QPixmap()
    if logo_path is not None:
        try:
            # Loading bytes bypasses Qt's filename cache when a logo is replaced.
            with open(logo_path, 'rb') as stream:
                supplied.loadFromData(stream.read())
        except OSError:
            pass
    if not supplied.isNull():
        side = max(1, round(pixels * .91))
        mark = supplied.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(pixmap)
        painter.drawPixmap((pixels - mark.width()) // 2, (pixels - mark.height()) // 2, mark)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        return pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(pixels / 48, pixels / 48)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#f1f1f1'))
    painter.drawRoundedRect(QRectF(1, 1, 46, 46), 13, 13)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen('#262626', 1.8))
    path = QPainterPath(QPointF(8, 24))
    path.cubicTo(15, 12.7, 33, 12.7, 40, 24)
    path.cubicTo(33, 35.3, 15, 35.3, 8, 24)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawEllipse(QPointF(24, 24), 4.7, 4.7)
    painter.setPen(_pen('#262626', 1.4))
    _line(painter, 24, 11.5, 24, 15.2)
    _line(painter, 24, 32.8, 24, 36.5)
    _line(painter, 5.8, 24, 10.2, 24)
    _line(painter, 37.8, 24, 42.2, 24)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def brand_pixmap(size=48):
    """Scale the supplied mark to logical pixels while preserving its proportions."""
    _check_gui()
    screen = QGuiApplication.primaryScreen()
    ratio = float(screen.devicePixelRatio()) if screen is not None else 1.
    path = None; stamp = None
    for candidate in (RESOURCE_ROOT / 'logo/logo.png', RESOURCE_ROOT / 'assets/logo.png'):
        if candidate.is_file():
            try:
                info = candidate.stat()
            except OSError:
                continue
            path = str(candidate)
            stamp = (info.st_mtime_ns, info.st_size)
            break
    return QPixmap(_brand_pixmap(_size(size), ratio, path, stamp))

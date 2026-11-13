"""Scalable lavender illustrations and toolbar icons for the processor."""
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QIcon, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap, QRadialGradient)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui_symbols import ICON_NAMES, icon


def _path(start, *segments, closed=False):
    path = QPainterPath(QPointF(*start))
    for segment in segments:
        if len(segment) == 2:
            path.lineTo(*segment)
        elif len(segment) == 4:
            path.quadTo(*segment)
        else:
            path.cubicTo(*segment)
    if closed:
        path.closeSubpath()
    return path


def _pen(color, width):
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _gradient(start, end, *colors):
    fill = QLinearGradient(QPointF(*start), QPointF(*end))
    for index, color in enumerate(colors):
        fill.setColorAt(index / (len(colors) - 1), QColor(color))
    return fill


def _dot(painter, x, y, radius, color):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(x, y), radius, radius)


def _halo(painter, x, y, radius):
    glow = QRadialGradient(QPointF(x, y), radius)
    glow.setColorAt(0, QColor('#BBB4FC'))
    glow.setColorAt(.65, QColor('#69D6D0FF'))
    glow.setColorAt(1, QColor('#00E2DEFF'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(x, y), radius, radius)


def _brain(painter):
    stem = _path((98, 72), (126, 72), (117, 85, 121, 100, 112, 107),
                 (110, 93, 101, 95, 98, 72), closed=True)
    painter.fillPath(stem, _gradient((97, 74), (121, 106), '#A79BFA', '#EDE9FF'))
    cortex = _path((51, 78), (37, 79, 33, 69, 38, 61), (31, 52, 37, 41, 45, 39),
                   (45, 27, 54, 24, 63, 25), (65, 17, 77, 12, 88, 18),
                   (95, 11, 106, 12, 113, 19), (124, 18, 133, 26, 134, 35),
                   (145, 39, 149, 49, 144, 59), (151, 69, 145, 80, 133, 82),
                   (128, 91, 115, 94, 106, 88), (99, 96, 85, 94, 79, 88),
                   (66, 92, 56, 87, 51, 78), closed=True)
    painter.setPen(_pen('#DBD5FE', 1.1))
    painter.setBrush(_gradient((53, 25), (132, 88), '#EFEAFF', '#C3BAFE', '#9A8CF7'))
    painter.drawPath(cortex)
    contours = (
        _path((87, 19), (96, 25, 87, 33, 91, 39), (97, 49, 86, 50, 89, 59), (92, 68, 85, 78, 80, 85)),
        _path((62, 26), (59, 38, 69, 39, 76, 33), (82, 31, 84, 37, 81, 42)),
        _path((46, 39), (55, 37, 59, 41, 58, 47), (56, 56, 63, 60, 71, 57), (77, 55, 81, 61, 76, 67)),
        _path((39, 61), (45, 62, 47, 58, 47, 53)),
        _path((52, 77), (58, 68, 69, 73, 70, 80)),
        _path((113, 20), (109, 28, 112, 31, 119, 34), (126, 36, 128, 41, 125, 46)),
        _path((97, 34), (104, 34, 107, 39, 106, 45), (105, 52, 113, 54, 119, 51)),
        _path((134, 36), (129, 45, 138, 49, 138, 55), (137, 64, 127, 63, 121, 63),
              (113, 62, 111, 70, 115, 76)),
        _path((90, 65), (99, 59, 105, 64, 102, 71), (99, 78, 105, 81, 109, 84)),
        _path((133, 80), (130, 72, 124, 74, 120, 80)),
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen('#B99DF2', 2.5))
    painter.save()
    painter.translate(.3, .8)
    for contour in contours:
        painter.drawPath(contour)
    painter.restore()
    painter.setPen(_pen('#EEEAFE', 1.55))
    for contour in contours:
        painter.drawPath(contour)


def _brain_orbit(painter):
    _halo(painter, 92, 61, 57)
    orbit_back = _path((22, 70), (48, 45, 146, 37, 154, 54))
    painter.setPen(_pen('#C7BCFB', 1.15))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(orbit_back)
    _brain(painter)
    orbit_front = _path((22, 70), (5, 91, 51, 103, 112, 82), (139, 72, 156, 65, 154, 54))
    painter.setPen(_pen('#9385F1', 1.5))
    painter.drawPath(orbit_front)
    for x, y, radius, color in ((55, 93, 4.7, '#9B8EF4'), (148, 35, 3.8, '#A69AF9'),
                                 (150, 89, 3.5, '#AA9DF7'), (26, 73, 2.2, '#C7BAFC')):
        _dot(painter, x, y, radius, color)


def _monitor_art(painter):
    _halo(painter, 95, 83, 77)
    head = _path((11, 165), (14, 146, 31, 137, 56, 130), (65, 127, 67, 118, 64, 111),
                 (47, 114, 31, 111, 30, 103), (29, 98, 24, 99, 25, 94),
                 (25, 91, 21, 90, 22, 86), (11, 85, 10, 80, 15, 74),
                 (25, 64, 24, 55, 31, 43), (40, 15, 69, 6, 97, 12),
                 (126, 17, 145, 39, 144, 66), (143, 91, 128, 105, 116, 116),
                 (109, 129, 118, 144, 132, 165), closed=True)
    painter.fillPath(head, _gradient((45, 26), (112, 164), '#C0B5FB', '#D9D1FE', '#E8E5FF'))
    painter.save()
    painter.setClipPath(head)
    painter.translate(10, 7)
    painter.scale(.88, .76)
    _brain(painter)
    painter.restore()

    signal = _path((28, 69), (55, 84, 78, 65, 96, 77), (120, 91, 128, 92, 143, 71),
                   (159, 57), (173, 67), (194, 53))
    painter.setPen(_pen('#A194F5', 1.55))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(signal)
    for x, y, r in ((159, 57, 3.4), (173, 67, 2.7), (194, 53, 3.9), (170, 39, 2.5)):
        _dot(painter, x, y, r, '#A497F7')
    almond = _path((55, 77), (67, 65, 76, 65, 89, 77), (76, 89, 67, 89, 55, 77), closed=True)
    painter.setPen(_pen('#F8F7FF', 1.4))
    painter.setBrush(_gradient((58, 67), (87, 89), '#F3EEFF', '#C5BAFE'))
    painter.drawPath(almond)
    _dot(painter, 72, 77, 6, '#9383F2')
    _dot(painter, 74, 75, 1.75, '#FFFFFF')

    stand = _path((145, 148), (165, 148), (162, 164), (171, 165), (173, 168, 170, 169),
                  (133, 169), (128, 168, 132, 165), (141, 164), closed=True)
    painter.fillPath(stand, _gradient((142, 150), (152, 171), '#D8D0FF', '#AFA1FC'))
    display = _path((118, 88), (201, 80), (205, 81, 204, 86), (193, 149),
                    (192, 154, 188, 154), (107, 153), (103, 152, 104, 147),
                    (114, 94), (115, 89, 118, 88), closed=True)
    painter.setPen(_pen('#FAF9FF', 2.6))
    painter.setBrush(_gradient((117, 87), (194, 154), '#FBFAFF', '#D4CCFC'))
    painter.drawPath(display)
    screen = _path((122, 95), (196, 88), (187, 145), (111, 146), closed=True)
    painter.fillPath(screen, _gradient((125, 96), (189, 145), '#ACA0F7', '#C7BDFB'))
    painter.save()
    painter.setClipPath(screen)
    painter.setPen(_pen('#66FFFFFF', .8))
    for y in (106, 117, 128, 139):
        painter.drawLine(QPointF(111, y), QPointF(194, y - 3))
    for x in (125, 139, 153, 167, 181):
        painter.drawLine(QPointF(x, 91), QPointF(x - 9, 148))
    trend = _path((117, 134), (128, 127), (138, 131), (148, 121), (155, 123),
                  (165, 109), (172, 119), (178, 122), (187, 108))
    painter.setPen(_pen('#FAF8FF', 1.75))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(trend)
    painter.restore()


class ProcessorIllustration(QWidget):
    def __init__(self, illustration, parent=None):
        super().__init__(parent)
        if illustration == 'brain':
            self._design_size, self._paint = QSize(168, 114), _brain_orbit
        elif illustration == 'monitor':
            self._design_size, self._paint = QSize(212, 177), _monitor_art
        else:
            raise ValueError(f'Unknown processor illustration: {illustration}')
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self):
        return self._design_size

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self._design_size.width(), self._design_size.height()
        scale = min(self.width() / width, self.height() / height)
        painter.translate((self.width() - width * scale) / 2, (self.height() - height * scale) / 2)
        painter.scale(scale, scale)
        self._paint(painter)
        painter.end()


def _extra_icon(painter, name):
    if name == 'search':
        painter.drawEllipse(QRectF(3.5, 3.5, 13, 13))
        painter.drawLine(QPointF(15, 15), QPointF(21, 21))
    elif name == 'info':
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawLine(QPointF(12, 11), QPointF(12, 16.5))
        painter.drawPoint(QPointF(12, 7.5))
    elif name == 'layers':
        painter.drawPath(_path((3, 8), (12, 3), (21, 8), (12, 13), closed=True))
        for y in (12, 16):
            painter.drawPath(_path((3, y), (12, y + 5), (21, y)))
    elif name in ('cpu', 'gpu'):
        painter.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 1.8, 1.8)
        painter.drawRect(QRectF(9, 9, 6, 6))
        for offset in (8.5, 12, 15.5):
            painter.drawLine(QPointF(offset, 3), QPointF(offset, 5.5))
            painter.drawLine(QPointF(offset, 18.5), QPointF(offset, 21))
            painter.drawLine(QPointF(3, offset), QPointF(5.5, offset))
            painter.drawLine(QPointF(18.5, offset), QPointF(21, offset))
    elif name == 'code':
        painter.drawPath(_path((8, 7), (3, 12), (8, 17)))
        painter.drawPath(_path((16, 7), (21, 12), (16, 17)))
        painter.drawLine(QPointF(14, 5), QPointF(10, 19))
    elif name == 'link':
        painter.drawPath(_path((8, 13), (5, 16), (3, 18, 6, 21, 8, 19), (12, 15), (13, 13, 12, 11)))
        painter.drawPath(_path((16, 11), (19, 8), (21, 6, 18, 3, 16, 5), (12, 9), (11, 11, 12, 13)))
        painter.drawLine(QPointF(9, 15), QPointF(15, 9))
    elif name == 'activity':
        painter.drawPath(_path((2, 12), (7, 12), (10, 5), (13, 19), (16, 12), (22, 12)))
    elif name == 'list':
        painter.drawRoundedRect(QRectF(5, 4.5, 14, 17), 2, 2)
        painter.drawRoundedRect(QRectF(9, 3, 6, 4), 1, 1)
        for y in (11, 15, 18):
            painter.drawLine(QPointF(8, y), QPointF(16, y))
    elif name == 'users':
        painter.drawEllipse(QRectF(5.5, 4.5, 6.5, 6.5))
        painter.drawPath(_path((3, 20), (3, 12, 15, 12, 15, 20)))
        painter.drawPath(_path((15, 5), (20, 5, 20, 11, 15, 11)))
        painter.drawPath(_path((17, 14), (20, 14, 21, 17, 21, 20)))
    elif name == 'brain':
        painter.scale(.14, .18)
        _brain_orbit(painter)
    else:
        raise ValueError(f'Unknown processor icon: {name}')


@lru_cache(maxsize=128)
def _icon(name, color, size):
    result = QIcon()
    for ratio in (1, 2):
        pixmap = QPixmap(size * ratio, size * ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size * ratio / 24, size * ratio / 24)
        painter.setPen(_pen(color, 1.65))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _extra_icon(painter, name)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        result.addPixmap(pixmap)
    return result


def processor_icon(name, color='#8378E9', size=20):
    if name in ICON_NAMES:
        return icon(name, color, size)
    return QIcon(_icon(name, color, size))

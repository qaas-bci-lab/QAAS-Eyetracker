"""Three centred fixation targets, rendered with Qt without external assets.

Importing this module never creates a QApplication or starts a timer. Remaining
time is supplied by the experiment's existing clock; the preview is static.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget


TARGET_STYLES = ('dot', 'cross', 'countdown')
DEFAULT_TARGET_COLOR = '#35452e'


def display_dimensions(parameters):
    """Return effective DIP sizes (marker diameter, counter font, prompt font)."""
    scale=float(parameters.get('display_scale',1.))
    return (float(parameters.get('target_diameter_px',20.))*scale,
            float(parameters.get('countdown_font_px',14.))*scale,
            max(22.,float(parameters.get('hint_font_px',22.)))*scale)

def draw_hint(painter,rect,text,font_px=22.,target_center=None,diameter_px=20.):
    """Compact edge hint shared by real experiment and 1:1 preview."""
    width=min(max(360.,len(text)*font_px*.7+36),rect.width()-36.);height=max(28.,font_px*2.4)
    box=QRectF((rect.width()-width)/2,rect.height()-height-18,width,height)
    if target_center is not None and box.adjusted(-diameter_px,-diameter_px,diameter_px,diameter_px).contains(QPointF(target_center)):
        box.moveTop(18.)
    painter.save();font=QFont(painter.font());font.setPointSizeF(float(font_px)*72./painter.device().logicalDpiY());painter.setFont(font)
    painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#ffffff'));painter.drawRoundedRect(box,9,9)
    painter.setPen(QColor('#242522'));painter.drawText(box.adjusted(10,3,-10,-3),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,text);painter.restore();return box


def countdown_value(remaining_s=None) -> int:
    """Use whole seconds remaining, with 1 shown until the target disappears."""
    remaining = 3.0 if remaining_s is None else float(remaining_s)
    if not math.isfinite(remaining):
        raise ValueError('Remaining target time must be finite.')
    return max(1, math.ceil(remaining))


def _color(value) -> QColor:
    color = QColor(value)
    if not color.isValid():
        raise ValueError('Invalid fixation target color.')
    return color


def _pen(color, width):
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _cross(painter, center, radius):
    x, y = center.x(), center.y()
    painter.drawLine(QPointF(x - radius, y), QPointF(x + radius, y))
    painter.drawLine(QPointF(x, y - radius), QPointF(x, y + radius))


def _countdown(painter, center, color, remaining_s, radius, font_px=14):
    outer = max(1.0, radius-1.0)
    # The disc and four short ticks make the target's geometric centre explicit.
    # Both remain fixed while the numeral changes.
    painter.setBrush(QColor('#ffffff'))
    painter.setPen(_pen('#626b5e', .9))
    painter.drawEllipse(center, outer, outer)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(color, 1.5))
    painter.drawEllipse(center, outer, outer)
    for dx, dy in ((1., 0.), (-1., 0.), (0., 1.), (0., -1.)):
        painter.drawLine(QPointF(center.x() + dx * (outer - 3.),
                                center.y() + dy * (outer - 3.)),
                         QPointF(center.x() + dx * outer,
                                 center.y() + dy * outer))

    text = str(countdown_value(remaining_s))
    font = QFont(painter.font())
    font.setPointSizeF(max(1.,float(font_px))*72./painter.device().logicalDpiY())
    font.setWeight(QFont.Weight.DemiBold)
    numeral = QPainterPath()
    numeral.addText(QPointF(0., 0.), font, text)
    bounds = numeral.boundingRect()
    # Fit the ink into one fixed, centred rectangle. This also handles long
    # formal-recording countdowns such as 480 or 3600 without shifting the mark.
    label_rect = QRectF(center.x() - outer * .93, center.y() - outer * .70,
                        outer * 1.86, outer * 1.40)
    scale = min(1., label_rect.width() / max(1., bounds.width()),
                label_rect.height() / max(1., bounds.height()))
    painter.save()
    painter.translate(label_rect.center())
    painter.scale(scale, scale)
    painter.translate(-bounds.center())
    # A fine opposite-luminance outline preserves white/yellow colour choices
    # on the disc, and dark choices remain crisp without a large shadow.
    luminance = .2126 * color.redF() + .7152 * color.greenF() + .0722 * color.blueF()
    contrast = '#4e5749' if luminance > .72 else '#ffffff'
    painter.setPen(_pen(contrast, .9 / scale))
    painter.setBrush(color)
    painter.drawPath(numeral)
    painter.restore()


def draw_target(painter: QPainter, center: QPointF, style='dot',
                color=DEFAULT_TARGET_COLOR, remaining_s=None, radius=10, *,diameter_px=None,countdown_font_px=14) -> None:
    """Paint a point, cross or seconds counter around the exact same centre.

    Coordinates and radius are logical Qt pixels. The caller owns the clock and
    retains its painter state, including any high-DPI transform.
    """
    if style not in TARGET_STYLES:
        raise ValueError('Unknown fixation target style: ' + str(style))
    color = _color(color)
    radius = float(radius if diameter_px is None else diameter_px/2)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError('Target radius must be positive and finite.')
    center = QPointF(center)
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if style == 'dot':
            size = max(1., radius-1.)
            painter.setBrush(color)
            painter.setPen(_pen('#ffffff', min(2.,radius*.25)))
            painter.drawEllipse(center, size, size)
            painter.setPen(_pen('#4e5749', .8))
            painter.drawEllipse(center, size, size)
        elif style == 'cross':
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for stroke, width in (('#ffffff', 6.), ('#4e5749', 4.2), (color, 2.6)):
                painter.setPen(_pen(stroke, width))
                _cross(painter, center, max(1.,radius-3.))
        else:
            _countdown(painter, center, color, remaining_s, radius,countdown_font_px)
    finally:
        painter.restore()


DIRECTIONS = ('up', 'down', 'left', 'right')


def draw_direction_arrow(painter: QPainter, center: QPointF, direction: str, size=64.) -> None:
    """Draw a crisp vector arrow without relying on fonts or platform emoji."""
    if direction not in DIRECTIONS:
        raise ValueError('Unknown direction: ' + str(direction))
    size = float(size)
    if not math.isfinite(size) or size <= 0:
        raise ValueError('Direction arrow size must be positive and finite.')
    path = QPainterPath()
    points = [(-.50, -.15), (.10, -.15), (.10, -.34), (.52, 0.),
              (.10, .34), (.10, .15), (-.50, .15)]
    path.moveTo(QPointF(points[0][0] * size, points[0][1] * size))
    for x, y in points[1:]:
        path.lineTo(QPointF(x * size, y * size))
    path.closeSubpath()
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(QPointF(center))
        painter.rotate({'right': 0., 'down': 90., 'left': 180., 'up': -90.}[direction])
        painter.setBrush(QColor('#242424'))
        painter.setPen(_pen('#ffffff', max(.4, size * .10)))
        painter.drawPath(path)
        painter.setPen(_pen('#242424', max(.3, size * .025)))
        painter.drawPath(path)
    finally:
        painter.restore()


def draw_direction_keypad(painter: QPainter, center: QPointF, key_size=64.) -> None:
    """Paint an aligned four-key arrow cluster for the response screen."""
    key_size = float(key_size)
    if not math.isfinite(key_size) or key_size <= 0:
        raise ValueError('Direction key size must be positive and finite.')
    gap = key_size * .16
    step = key_size + gap
    positions = {'up': (0., -step), 'left': (-step, 0.),
                 'down': (0., 0.), 'right': (step, 0.)}
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for direction, (dx, dy) in positions.items():
            key_center = QPointF(center.x()+dx, center.y()+dy)
            rect = QRectF(key_center.x()-key_size/2, key_center.y()-key_size/2,
                          key_size, key_size)
            painter.setPen(_pen('#d4d4d8', 1.2))
            painter.setBrush(QColor('#ffffff'))
            painter.drawRoundedRect(rect, key_size*.16, key_size*.16)
            draw_direction_arrow(painter, key_center, direction, key_size*.42)
    finally:
        painter.restore()


class TargetPreview(QWidget):
    """Static settings preview, with no independent timer or experiment state."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.style = 'dot'
        self.color = DEFAULT_TARGET_COLOR
        self.diameter_px=20.;self.countdown_font_px=14.
        self.background_color='#f3f3f3';self.background_image=QPixmap()
        self.setMinimumSize(160, 90)
        self.setAccessibleName('标定点外观预览')

    def set_target(self, style, color):
        if style not in TARGET_STYLES:
            raise ValueError('Unknown fixation target style: ' + str(style))
        self.color = _color(color).name()
        self.style = style
        self.update()

    def set_dimensions(self,diameter_px=20.,countdown_font_px=14.):
        self.diameter_px=float(diameter_px);self.countdown_font_px=float(countdown_font_px);self.update()

    def set_background(self,color,image=None):
        self.background_color=color
        self.background_image=QPixmap(str(image)) if image else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.background_color) if self.background_image.isNull() else QColor('#151515'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 10., 10.)
        if not self.background_image.isNull():
            size=self.background_image.size();size.scale(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
            painter.drawPixmap((self.width()-size.width())//2,(self.height()-size.height())//2,
                self.background_image.scaled(size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        draw_target(painter, QPointF(self.width() / 2., self.height() / 2.),
                    self.style, self.color, remaining_s=3.,diameter_px=self.diameter_px,countdown_font_px=self.countdown_font_px)

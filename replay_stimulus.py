"""Reconstruct the fixation stimulus from the recorded experiment settings."""
import math
import re

import cv2


_AA = cv2.LINE_AA
_WHITE = (255, 255, 255)
_OUTLINE = (73, 87, 78)
_SHIFT = 4


def _positive(value, default):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _point(x, y):
    return round(x * (1 << _SHIFT)), round(y * (1 << _SHIFT))


class ReplayStimulus:
    """Return the image shown underneath the gaze point, without needing Qt."""

    def __init__(self, background, meta, geometry):
        self._background = background
        self._image = background
        self._second = None
        self.binding = None
        scheme = meta.get('scheme') or {}
        if meta.get('fixation_enabled', scheme.get('fixation_enabled')) is not True:
            return
        style = scheme.get('fixation_style', 'cross')
        if style not in ('dot', 'cross', 'countdown'):
            return
        color = scheme.get('fixation_color', '#202020')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            color = '#202020'
        cfg = meta.get('config') or {}
        display = cfg.get('display_snapshot') or {}
        logical_width = _positive(cfg.get('logical_width'), 0.)
        default_dpr = geometry['width'] / logical_width if logical_width else 1.
        dpr = _positive(cfg.get('dpr'), _positive(display.get('device_pixel_ratio'), default_dpr))
        height, width = background.shape[:2]
        self._x, self._y = width * geometry['ox'], height * geometry['oy']
        self._pixel_scale = width / geometry['width'] * dpr
        scale = min(4., _positive(scheme.get('display_scale'), 1.))
        diameter = min(200., _positive(scheme.get('fixation_diameter_px'), 20.)) * scale
        self._radius = diameter / 2
        self._color = tuple(int(color[index:index + 2], 16) for index in (5, 3, 1))
        self.binding = dict(style=style, color=color.lower(), diameter_px=diameter, dpr=dpr)
        if style == 'countdown':
            minutes = _positive(scheme.get('duration_minutes'), 0.)
            duration = _positive(meta.get('planned_duration_s'), minutes * 60)
            self._duration = min(86400., duration or _positive(meta.get('actual_duration_s'), 1.))
            font = min(96., _positive(scheme.get('countdown_font_px'), 14.)) * scale
            self.binding.update(planned_duration_s=self._duration, countdown_font_px=font)
            self._font_height = font * self._pixel_scale
        else:
            self._image = self._paint()

    def at(self, elapsed_s):
        if self.binding and self.binding['style'] == 'countdown':
            second = max(1, math.ceil(self._duration - elapsed_s))
            if second != self._second:
                self._image = self._paint(second)
                self._second = second
        return self._image

    def _thickness(self, pixels):
        return max(1, round(pixels * self._pixel_scale))

    def _paint(self, second=None):
        image = self._background.copy()
        center = _point(self._x, self._y)
        scale = self._pixel_scale
        radius = max(1., self._radius - 1.) * scale
        if self.binding['style'] == 'cross':
            arm = max(1., self._radius - 3.) * scale
            for color, stroke in ((_WHITE, 6.), (_OUTLINE, 4.2), (self._color, 2.6)):
                thickness = self._thickness(stroke)
                cv2.line(image, _point(self._x - arm, self._y), _point(self._x + arm, self._y),
                         color, thickness, _AA, _SHIFT)
                cv2.line(image, _point(self._x, self._y - arm), _point(self._x, self._y + arm),
                         color, thickness, _AA, _SHIFT)
        elif second is None:
            cv2.circle(image, center, round(radius * (1 << _SHIFT)), self._color, -1, _AA, _SHIFT)
            cv2.circle(image, center, round(radius * (1 << _SHIFT)), _WHITE, self._thickness(2.), _AA, _SHIFT)
            cv2.circle(image, center, round(radius * (1 << _SHIFT)), _OUTLINE, self._thickness(.8), _AA, _SHIFT)
        else:
            self._paint_countdown(image, center, radius, second)
        return image

    def _paint_countdown(self, image, center, radius, second):
        encoded_radius = round(radius * (1 << _SHIFT))
        cv2.circle(image, center, encoded_radius, _WHITE, -1, _AA, _SHIFT)
        cv2.circle(image, center, encoded_radius, self._color, self._thickness(1.5), _AA, _SHIFT)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tick = max(0., radius - 3 * self._pixel_scale)
            cv2.line(image, _point(self._x + dx * tick, self._y + dy * tick),
                     _point(self._x + dx * radius, self._y + dy * radius),
                     self._color, self._thickness(1.5), _AA, _SHIFT)
        text = str(second)
        font = cv2.FONT_HERSHEY_SIMPLEX
        (width, height), _ = cv2.getTextSize(text, font, 1., 1)
        font_scale = min(radius * 1.86 / width, radius * 1.4 / height, self._font_height / height)
        (width, height), _ = cv2.getTextSize(text, font, font_scale, 1)
        origin = (round(self._x - width / 2), round(self._y + height / 2))
        blue, green, red = self._color
        outline = _OUTLINE if .2126 * red + .7152 * green + .0722 * blue > .72 * 255 else _WHITE
        cv2.putText(image, text, origin, font, font_scale, outline, self._thickness(2.), _AA)
        cv2.putText(image, text, origin, font, font_scale, self._color, self._thickness(1.), _AA)

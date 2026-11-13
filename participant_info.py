"""Compact participant details for a single recording, without persistence."""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QIntValidator, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QRadioButton,
    QWidget,
)


class _SexButton(QRadioButton):
    """Keep the whole outlined segment clickable, including its padding."""

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            background, foreground, border = '#f0f0f1', '#a1a1aa', '#e4e4e7'
        elif self.isChecked():
            background, foreground, border = '#27272a', '#ffffff', '#27272a'
        else:
            background = '#f1f1f2' if self.underMouse() else '#fcfcfc'
            foreground = '#18181b' if self.underMouse() else '#3f3f46'
            border = '#a1a1aa' if self.underMouse() else '#dedee1'
        if self.hasFocus():
            border = '#52525b'
        painter.setPen(QPen(QColor(border), 1.5 if self.hasFocus() else 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.75, .75, -.75, -.75), 10, 10)
        painter.setPen(QColor(foreground))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class ParticipantInfo(QWidget):
    """Two rows of optional details shared by experiment and test starts.

    ``values()`` returns a detached dictionary suitable for session metadata.
    Blank age is ``None``; zero is a valid entered age. No field is persisted by
    this widget, and no sex is preselected.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._person_key = None
        self.setObjectName('ParticipantInfo')
        self.setAccessibleName('被试基本信息')
        self.name_input = QLineEdit(self)
        self.name_input.setAccessibleName('被试姓名')
        self.name_input.setPlaceholderText('填写姓名')
        self.name_input.setMinimumWidth(0)

        self.age_input = QLineEdit(self)
        self.age_input.setAccessibleName('被试年龄')
        self.age_input.setPlaceholderText('岁')
        self.age_input.setValidator(QIntValidator(0, 120, self.age_input))
        self.age_input.setFixedWidth(94)
        self.age_input.setToolTip('0～120 岁，可以留空')

        self.id_input = QLineEdit(self)
        self.id_input.setAccessibleName('被试编号')
        self.id_input.setPlaceholderText('如 001')
        self.id_input.setMinimumWidth(0)

        sex_widget = QWidget(self)
        sex_widget.setAccessibleName('被试性别')
        sex_row = QHBoxLayout(sex_widget)
        sex_row.setContentsMargins(0, 0, 0, 0)
        sex_row.setSpacing(6)
        self.sex_group = QButtonGroup(self)
        self.sex_group.setExclusive(True)
        self.male_button = _SexButton('男', sex_widget)
        self.female_button = _SexButton('女', sex_widget)
        for button in (self.male_button, self.female_button):
            button.setAccessibleName('性别' + button.text())
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(44, 40)
            self.sex_group.addButton(button)
            sex_row.addWidget(button)
        sex_widget.setFixedWidth(94)

        form = QGridLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)
        for row, left_title, left_field, right_title, right_field in (
            (0, '姓名', self.name_input, '年龄', self.age_input),
            (1, '编号', self.id_input, '性别', sex_widget),
        ):
            left_label = QLabel(left_title, self)
            left_label.setBuddy(left_field)
            right_label = QLabel(right_title, self)
            right_label.setBuddy(self.age_input if row == 0 else self.male_button)
            form.addWidget(left_label, row, 0)
            form.addWidget(left_field, row, 1)
            form.addWidget(right_label, row, 2)
            form.addWidget(right_field, row, 3)
        QWidget.setTabOrder(self.name_input, self.age_input)
        QWidget.setTabOrder(self.age_input, self.id_input)
        QWidget.setTabOrder(self.id_input, self.male_button)
        QWidget.setTabOrder(self.male_button, self.female_button)

    def values(self):
        """Return details, rejecting an entered age outside 0–120 integers."""
        text = self.age_input.text().strip()
        if text and (re.fullmatch(r'[0-9]+', text) is None or not 0 <= int(text) <= 120):
            raise ValueError('年龄请填写 0～120 的整数，或留空。')
        selected = self.sex_group.checkedButton()
        values = {
            'name': self.name_input.text().strip(),
            'age': int(text) if text else None,
            'sex': selected.text() if selected is not None else '',
            'participant_id': self.id_input.text().strip(),
        }
        if self._person_key:
            values['person_key'] = self._person_key
        return values

    def set_values(self, values=None):
        """Load a saved profile while keeping its stable identity for recordings."""
        values = values or {}
        self._person_key = values.get('person_key') or values.get('key')
        self.name_input.setText(str(values.get('name') or ''))
        self.id_input.setText(str(values.get('participant_id') or ''))
        age = values.get('age')
        self.age_input.setText('' if age is None else str(age))
        self.sex_group.setExclusive(False)
        for button in self.sex_group.buttons():
            button.setChecked(button.text() == values.get('sex'))
        self.sex_group.setExclusive(True)

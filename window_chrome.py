"""Light application title bars for normal windows and dialogs."""

from PyQt6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMainWindow, QToolButton, QVBoxLayout, QWidget

from ui_symbols import brand_pixmap


class CaptionButton(QToolButton):
    def __init__(self, glyph, title, action, parent):
        super().__init__(parent)
        self.glyph = glyph
        self.setToolTip(title)
        self.setAccessibleName(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 32)
        self.clicked.connect(action)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.isDown() or self.hasFocus():
            painter.setPen(Qt.PenStyle.NoPen)
            close_hover = self.glyph == 'close' and self.underMouse()
            painter.setBrush(QColor('#DDDDDD' if self.isDown() else '#EAEAEA' if close_hover else '#F5F5F5'))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 1, -2, -1), 8, 8)
        painter.translate(self.width() / 2, self.height() / 2)
        color = '#272727' if self.glyph == 'close' and self.underMouse() else '#666666'
        painter.setPen(QPen(QColor(color), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.glyph == 'close':
            painter.drawLine(-4, -4, 4, 4)
            painter.drawLine(4, -4, -4, 4)
        elif self.glyph == 'minimize':
            painter.drawLine(-4, 0, 4, 0)
        elif self.window().isMaximized():
            painter.drawPolyline(QPoint(0, -5), QPoint(5, -5), QPoint(5, 0))
            painter.drawRect(-5, -2, 7, 7)
        else:
            painter.drawRect(-4, -4, 8, 8)


class TitleBar(QWidget):
    def __init__(self, window, maximizable):
        super().__init__(window)
        self._window = window
        self._maximizable = maximizable
        self._drag_offset = None
        self.setFixedHeight(44)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet('TitleBar { background:#FFFFFF; border-bottom:1px solid #DDDDDD; }')
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(9)

        mark = QLabel(self)
        mark.setPixmap(brand_pixmap(28))
        mark.setFixedSize(28, 28)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(mark)

        title = QLabel(window.windowTitle(), self)
        title.setStyleSheet('font-size:14px; font-weight:600; color:#666666;')
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        window.windowTitleChanged.connect(title.setText)
        row.addWidget(title)
        row.addStretch(1)

        if maximizable:
            row.addWidget(CaptionButton('minimize', '最小化', window.showMinimized, self))
            maximize = CaptionButton('maximize', '最大化或还原', self.toggle_maximized, self)
            row.addWidget(maximize)
        row.addWidget(CaptionButton('close', '关闭窗口', window.close, self))

    def toggle_maximized(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def mouseDoubleClickEvent(self, event):
        if self._maximizable and event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        handle = self._window.windowHandle()
        if handle is None or not handle.startSystemMove():
            if not self._window.isMaximized():
                self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class ResizeHandle(QWidget):
    def __init__(self, window, edges, cursor):
        super().__init__(window)
        self._edges = edges
        self.setCursor(cursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event):
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            if handle.startSystemResize(self._edges):
                event.accept()
                return
        super().mousePressEvent(event)


class ResizeBorder(QObject):
    def __init__(self, window):
        super().__init__(window)
        edge = Qt.Edge
        cursor = Qt.CursorShape
        self._handles = [ResizeHandle(window, edges, shape) for edges, shape in (
            (edge.TopEdge, cursor.SizeVerCursor),
            (edge.BottomEdge, cursor.SizeVerCursor),
            (edge.LeftEdge, cursor.SizeHorCursor),
            (edge.RightEdge, cursor.SizeHorCursor),
            (edge.TopEdge | edge.LeftEdge, cursor.SizeFDiagCursor),
            (edge.TopEdge | edge.RightEdge, cursor.SizeBDiagCursor),
            (edge.BottomEdge | edge.LeftEdge, cursor.SizeBDiagCursor),
            (edge.BottomEdge | edge.RightEdge, cursor.SizeFDiagCursor),
        )]
        window.installEventFilter(self)

    def eventFilter(self, window, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WindowStateChange):
            width, height = window.width(), window.height()
            inset = 5
            geometries = (
                (inset, 0, width - 2 * inset, inset),
                (inset, height - inset, width - 2 * inset, inset),
                (0, inset, inset, height - 2 * inset),
                (width - inset, inset, inset, height - 2 * inset),
                (0, 0, inset, inset), (width - inset, 0, inset, inset),
                (0, height - inset, inset, inset), (width - inset, height - inset, inset, inset),
            )
            enabled = not (window.isMaximized() or window.isFullScreen())
            for handle, geometry in zip(self._handles, geometries):
                handle.setGeometry(*geometry)
                handle.setVisible(enabled)
                if enabled:
                    handle.raise_()
        return False


def install_chrome(window, layout=None, *, resizable=True):
    """Install before showing, preserving the window's content and modality."""
    if getattr(window, '_app_titlebar', None) is not None:
        return window._app_titlebar
    main_window = isinstance(window, QMainWindow)
    if not main_window:
        layout = layout if layout is not None else window.layout()
        if layout is None:
            return None

    titlebar = TitleBar(window, main_window and resizable)
    window._app_titlebar = titlebar
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    if main_window:
        content = window.takeCentralWidget()
        surface = QWidget(window)
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        surface_layout.addWidget(titlebar)
        if content is not None:
            surface_layout.addWidget(content, 1)
        window.setCentralWidget(surface)
    else:
        layout.setMenuBar(titlebar)
    if resizable:
        window._resize_border = ResizeBorder(window)
    return titlebar


class DialogChrome(QObject):
    def eventFilter(self, dialog, event):
        if (event.type() == QEvent.Type.Polish and isinstance(dialog, QDialog)
                and dialog.isWindow() and dialog.layout() is not None
                and not dialog.isFullScreen() and not dialog.property('plain_fullscreen')):
            install_chrome(dialog, resizable=False)
        return False


def install_dialog_chrome(application):
    """Style Qt's local message boxes and pickers as well as custom dialogs."""
    if not hasattr(application, '_app_dialog_chrome'):
        application.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
        application._app_dialog_chrome = DialogChrome(application)
        application.installEventFilter(application._app_dialog_chrome)

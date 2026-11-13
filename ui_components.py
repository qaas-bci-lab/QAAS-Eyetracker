"""PyQt6 presentation and scheme editor. No camera or inference operations here."""
from __future__ import annotations
import copy,os,sys
from pathlib import Path
from PyQt6.QtCore import Qt,QRectF,QPointF,QSize,QObject,QRunnable,QThreadPool,QTimer,pyqtSignal
from PyQt6.QtGui import (QFont,QFontDatabase,QPalette,QColor,QPixmap,QPainter,QPen,QIcon,
    QLinearGradient,QRadialGradient)
from PyQt6.QtWidgets import (QApplication,QWidget,QDialog,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,
    QFormLayout,QComboBox,QLineEdit,QDoubleSpinBox,QSpinBox,QCheckBox,QFileDialog,QMessageBox,QListWidget,
    QTabWidget,QTabBar,QScrollArea,QInputDialog,QTableWidget,QTableWidgetItem,QHeaderView,QSizePolicy)
from scheme_store import (default_scheme, save_settings, validate_scheme, resolve_image,
                          import_image, preset_points, normalize_settings, validate_processing,
                          processing_for_mode, preferred_model, DEFAULT_REST_MINUTES)
from app_paths import DATA_ROOT, RAW_CAPTURE, RESOURCE_ROOT
from ui_symbols import icon,brand_pixmap
from window_chrome import install_chrome,install_dialog_chrome
from target_visuals import draw_target,draw_hint,display_dimensions

ROOT=RESOURCE_ROOT
MODEL_NAMES={'affnet_mixed':'AFF-Net 均衡混合（屏幕校准）'}
_PROCESSING_PROBES=set()


class _HardwareProbeSignals(QObject):
    ready=pyqtSignal(object)
    finished=pyqtSignal()


class _HardwareProbe(QRunnable):
    """Qt's application pool owns active probes after their settings dialog closes."""
    def __init__(self):
        super().__init__();self.signals=_HardwareProbeSignals()

    def run(self):
        try:
            from processing_hardware import probe_hardware
            result=probe_hardware()
        except Exception as error:
            result=dict(cpu_count=max(1,os.cpu_count() or 1),cuda_available=False,
                        cuda_devices=[],cuda_reason='无法检测 GPU：'+str(error))
        self.signals.ready.emit(result)
        self.signals.finished.emit()


STYLES='''
QWidget { color:#27272A; font-size:14px; }
QMainWindow,QDialog { background:#F5F5F6; }
QLabel { background:transparent; }
QLabel#Title { color:#18181B; font-size:29px; font-weight:700; }
QLabel#SectionTitle { color:#27272A; font-size:22px; font-weight:650; }
QLabel#CardTitle { color:#27272A; font-size:17px; font-weight:650; }
QLabel#Muted { color:#71717A; }
QLabel#Metric { color:#27272A; font-size:30px; font-weight:700; }
QLabel#Eyebrow { color:#8B8B91; font-size:12px; font-weight:600; }
QLabel#HeroTitle { color:#18181B; font-size:23px; font-weight:700; }
QLabel#HeroSubtitle { color:#71717A; font-size:13px; }
QLabel#FieldGroupTitle { color:#3F3F46; font-size:14px; font-weight:650; }
QLabel#SchemeBadge { color:#52525B; background:#F1F1F2; border:1px solid #E4E4E7; border-radius:11px; padding:3px 10px; font-size:12px; font-weight:600; }
QWidget#NavigationPill { background:rgba(255,255,255,195); border:1px solid rgba(255,255,255,230); border-radius:15px; }
QWidget#HomePanel { background:rgba(255,255,255,246); border:1px solid rgba(255,255,255,255); border-radius:24px; }
QFrame#Card { background:#FFFFFF; border:1px solid #E8E8EA; border-radius:18px; }
QFrame#Divider { background:#ECECEE; border:none; max-height:1px; }
QPushButton { min-height:40px; padding:0 17px; border-radius:11px; background:#FFFFFF; border:1px solid #DCDCDE; color:#3F3F46; font-weight:550; }
QPushButton:hover { background:#F5F5F5; border-color:#A1A1AA; color:#18181B; }
QPushButton:pressed { background:#EAEAEC; border-color:#8B8B91; }
QPushButton:focus { border:2px solid #52525B; }
QPushButton#StartEntry { min-height:164px; max-height:164px; min-width:160px; max-width:160px; padding:0; border:none; background:transparent; }
QPushButton#WelcomeEntry { min-height:154px; max-height:154px; padding:0; border:none; background:transparent; }
QPushButton:disabled { color:#A1A1AA; background:#F0F0F1; border-color:#E4E4E7; }
QPushButton[primary="true"] { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #48484D,stop:1 #111113); border:1px solid #18181B; color:white; font-weight:650; }
QPushButton[primary="true"]:hover { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5B5B60,stop:1 #232326); border-color:#343437; }
QPushButton[primary="true"]:pressed { background:#09090B; }
QPushButton[primary="true"]:disabled { background:#B9B9BC; border-color:#B9B9BC; color:#F4F4F5; }
QPushButton[quiet="true"] { background:transparent; border:1px solid transparent; color:#52525B; padding:0 12px; }
QPushButton[quiet="true"]:hover,QPushButton[quiet="true"]:focus { background:#EDEDEF; border-color:#E4E4E7; color:#18181B; }
QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox { min-height:40px; background:#FCFCFC; border:1px solid #DEDEE1; border-radius:11px; padding:0 12px; color:#27272A; selection-background-color:#3F3F46; selection-color:#FFFFFF; }
QComboBox:hover,QLineEdit:hover,QSpinBox:hover,QDoubleSpinBox:hover { border-color:#A1A1AA; background:#FFFFFF; }
QComboBox:focus,QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus { border:2px solid #52525B; background:#FFFFFF; }
QComboBox:disabled,QLineEdit:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled { background:#F0F0F1; color:#A1A1AA; }
QComboBox::drop-down { width:32px; border:none; }
QComboBox::down-arrow { image:url(CHEVRON_PATH); width:14px; height:14px; }
QSpinBox::up-button,QDoubleSpinBox::up-button { subcontrol-origin:border; subcontrol-position:top right; width:26px; border:none; background:transparent; }
QSpinBox::down-button,QDoubleSpinBox::down-button { subcontrol-origin:border; subcontrol-position:bottom right; width:26px; border:none; background:transparent; }
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow { image:url(CHEVRON_UP_PATH); width:10px; height:10px; }
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow { image:url(CHEVRON_PATH); width:10px; height:10px; }
QComboBox QAbstractItemView { background:#FFFFFF; color:#27272A; selection-background-color:#EAEAEC; selection-color:#18181B; padding:6px; border:1px solid #E4E4E7; border-radius:10px; outline:none; }
QCheckBox { spacing:10px; min-height:30px; }
QCheckBox::indicator { width:18px; height:18px; }
QListWidget { background:#FCFCFC; border:1px solid #E4E4E7; border-radius:14px; padding:7px; outline:none; }
QListWidget::item { min-height:40px; padding:3px 11px; border-radius:9px; }
QListWidget::item:hover { background:#F1F1F2; }
QListWidget::item:selected { background:#E7E7E9; color:#27272A; font-weight:600; }
QTabWidget::pane { border:0; }
QTabBar::tab { padding:10px 22px; background:transparent; color:#71717A; margin:0 10px 4px 0; border-bottom:2px solid transparent; }
QTabBar::tab:hover { color:#27272A; }
QTabBar::tab:selected { color:#18181B; border-bottom:2px solid #3F3F46; font-weight:650; }
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical { background:transparent; width:9px; margin:2px; }
QScrollBar::handle:vertical { background:#C9C9CD; border-radius:4px; min-height:32px; }
QScrollBar::handle:vertical:hover { background:#A1A1AA; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QProgressBar { border:none; border-radius:5px; background:#E7E7E9; min-height:8px; max-height:8px; color:transparent; }
QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #71717A,stop:1 #18181B); border-radius:5px; }
QTableWidget { background:#FFFFFF; border:1px solid #E4E4E7; border-radius:12px; gridline-color:#F0F0F1; selection-background-color:#ECECEE; selection-color:#27272A; alternate-background-color:#FAFAFA; }
QHeaderView::section { padding:9px; background:#F6F6F7; border:none; color:#626268; font-weight:600; }
QPlainTextEdit { background:#FCFCFC; border:1px solid #DEDEE1; border-radius:12px; padding:10px; selection-background-color:#3F3F46; }
QToolTip { color:#3F3F46; background:#FFFFFF; border:1px solid #DCDCDE; border-radius:7px; padding:7px; }
'''

def configure_system_font():
    if sys.platform=='win32':
        fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        for p in ['msyh.ttc','simhei.ttf','simsun.ttc']:
            if (fonts/p).exists():QFontDatabase.addApplicationFont(str(fonts/p))
    candidates=['Microsoft YaHei UI','Microsoft YaHei','PingFang SC','Noto Sans CJK SC','SimHei','SimSun']
    font=QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    existing=QFontDatabase.families();available=[f for f in candidates if f in existing]
    if available:font.setFamilies(available)
    font.setPointSize(11);app=QApplication.instance();app.setFont(font);app.setStyle('Fusion');app.setWindowIcon(QIcon(brand_pixmap(64)))
    install_dialog_chrome(app)
    palette=app.palette()
    for role,color in [(QPalette.ColorRole.Window,'#F5F5F6'),(QPalette.ColorRole.WindowText,'#27272A'),
        (QPalette.ColorRole.Base,'#FCFCFC'),(QPalette.ColorRole.Text,'#27272A'),
        (QPalette.ColorRole.Button,'#FFFFFF'),(QPalette.ColorRole.ButtonText,'#3F3F46'),
        (QPalette.ColorRole.Highlight,'#3F3F46'),(QPalette.ColorRole.HighlightedText,'#FFFFFF')]:palette.setColor(role,QColor(color))
    app.setPalette(palette);app.setStyleSheet(STYLES.replace('CHEVRON_UP_PATH',(ROOT/'assets/chevron_up.svg').as_posix()).replace('CHEVRON_PATH',(ROOT/'assets/chevron_down.svg').as_posix()))

def label(text='',kind=None,wrap=False):
    w=QLabel(text)
    if kind:w.setObjectName(kind)
    w.setWordWrap(wrap);return w

def button(text,callback=None,primary=False,quiet=False):
    w=QPushButton(text);w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.setProperty('primary',primary);w.setProperty('quiet',quiet)
    symbols={'新建':'plus','复制':'copy','删除':'trash','选择图片':'image','设置原点':'target',
        '恢复中心':'target','居中':'target','保存':'check','使用此原点':'check','相机标定':'camera',
        '导入参数':'folder','打开 TXT':'file','选择其他记录':'folder','生成分析':'chart',
        '播放':'play','打开视频':'play','完成':'check','开始实验':'play','数据分析':'chart','设置':'settings'}
    if text in symbols:
        w.setIcon(icon(symbols[text],color='#FFFFFF' if primary else '#52525B',size=20));w.setIconSize(QSize(18,18))
    if callback:w.clicked.connect(callback)
    return w

def page_heading(text,symbol=None,branded=False):
    """A small product mark or section icon beside a single, readable heading."""
    w=QWidget();row=QHBoxLayout(w);row.setContentsMargins(0,0,0,0);row.setSpacing(14)
    mark=QLabel();mark.setObjectName('BrandMark' if branded else 'SectionMark');mark.setFixedSize(48 if branded else 38,48 if branded else 38)
    mark.setPixmap(brand_pixmap(48) if branded else icon(symbol or 'eye',size=30).pixmap(30,30))
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter);row.addWidget(mark)
    row.addWidget(label(text,'Title' if branded else 'SectionTitle'));row.addStretch();return w

class WelcomeEntry(QPushButton):
    """Quiet icon entry, using the reference app's generous card proportions."""
    def __init__(self,text,symbol,callback=None):
        super().__init__(text);self.setIcon(icon(symbol,color='#242424',size=28));self.setIconSize(QSize(28,28))
        self.setMinimumWidth(160);self.setFixedHeight(154);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor);self.setAttribute(Qt.WidgetAttribute.WA_Hover,True)
        self.setAccessibleName(text);self.setObjectName('WelcomeEntry')
        if callback:self.clicked.connect(callback)
    def enterEvent(self,event):super().enterEvent(event);self.update()
    def leaveEvent(self,event):super().leaveEvent(event);self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        active=self.isEnabled();hover=active and self.underMouse();down=active and self.isDown()
        r=QRectF(self.rect()).adjusted(1,1,-1,-1)
        p.setBrush(QColor('#eeeeee' if down else '#fafafa' if hover else '#ffffff' if active else '#f2f2f2'))
        p.setPen(QPen(QColor('#adadad' if hover else '#e4e4e4'),1));p.drawRoundedRect(r,18,18)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor('#6c6c6c'),2));p.drawRoundedRect(r.adjusted(3,3,-3,-3),15,15)
        chip=QRectF((self.width()-48)/2,25,48,48)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#e8e8e8' if hover else '#f2f2f2'));p.drawRoundedRect(chip,14,14)
        self.icon().paint(p,chip.adjusted(10,10,-10,-10).toRect(),Qt.AlignmentFlag.AlignCenter,QIcon.Mode.Normal if active else QIcon.Mode.Disabled)
        font=QFont(self.font());font.setPixelSize(17);font.setWeight(QFont.Weight.DemiBold);p.setFont(font)
        p.setPen(QColor('#222222' if active else '#888888'));p.drawText(QRectF(12,92,self.width()-24,30),Qt.AlignmentFlag.AlignCenter,self.text())

class HomeSurface(QWidget):
    """Soft monochrome canvas used only by the home window, never by acquisition."""
    def paintEvent(self,event):
        from PyQt6.QtGui import QBrush
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base=QLinearGradient(0,0,self.width(),self.height())
        base.setColorAt(0,QColor('#FAFAFA'));base.setColorAt(.52,QColor('#F0F0F1'));base.setColorAt(1,QColor('#E4E4E7'))
        p.fillRect(self.rect(),QBrush(base))
        for x,y,radius,inner,outer in (
            (self.width()*.06,self.height()*.12,max(180,self.width()*.27),QColor(90,90,96,28),QColor(90,90,96,0)),
            (self.width()*.96,self.height()*.26,max(170,self.width()*.23),QColor(255,255,255,110),QColor(255,255,255,0)),
            (self.width()*.82,self.height()*.98,max(200,self.width()*.30),QColor(110,110,116,24),QColor(110,110,116,0)),
        ):
            glow=QRadialGradient(QPointF(x,y),radius);glow.setColorAt(0,inner);glow.setColorAt(1,outer)
            p.fillRect(QRectF(x-radius,y-radius,radius*2,radius*2),QBrush(glow))


class StartEntry(QPushButton):
    """A tactile monochrome launch control with a clear keyboard focus ring."""
    def __init__(self,callback=None):
        super().__init__('开始实验');self.setObjectName('StartEntry');self.setFixedSize(160,164)
        self.setIcon(icon('play',color='#FFFFFF',size=40));self.setAccessibleName('开始实验')
        self.setCursor(Qt.CursorShape.PointingHandCursor);self.setAttribute(Qt.WidgetAttribute.WA_Hover,True)
        if callback:self.clicked.connect(callback)
    def enterEvent(self,event):super().enterEvent(event);self.update()
    def leaveEvent(self,event):super().leaveEvent(event);self.update()
    def paintEvent(self,event):
        from PyQt6.QtGui import QBrush
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chip=QRectF(30,5,100,100);enabled=self.isEnabled();hover=enabled and self.underMouse();down=enabled and self.isDown()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0,0,0,10 if not enabled else 20));p.drawEllipse(chip.adjusted(-7,9,7,17))
        p.setBrush(QColor(0,0,0,12 if not enabled else 26));p.drawEllipse(chip.adjusted(-3,5,3,11))
        if enabled:
            gradient=QLinearGradient(chip.topLeft(),chip.bottomRight())
            gradient.setColorAt(0,QColor('#71717A' if hover else '#55555A'))
            gradient.setColorAt(1,QColor('#09090B' if down else '#18181B'))
            p.setBrush(QBrush(gradient))
        else:p.setBrush(QColor('#D4D4D8'))
        p.drawEllipse(chip)
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(255,255,255,75 if enabled else 35),1.2));p.drawEllipse(chip.adjusted(5,5,-5,-5))
        if hover and not down:
            p.setPen(QPen(QColor(40,40,43,35),2));p.drawEllipse(chip.adjusted(-5,-5,5,5))
        if self.hasFocus():
            p.setPen(QPen(QColor('#27272A'),2.5));p.drawEllipse(chip.adjusted(-6,-6,6,6))
        self.icon().paint(p,chip.adjusted(31,29,-31,-31).toRect(),Qt.AlignmentFlag.AlignCenter,QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled)
        font=QFont(self.font());font.setPixelSize(22);font.setWeight(QFont.Weight.DemiBold);p.setFont(font)
        p.setPen(QColor('#27272A' if enabled else '#A1A1AA'));p.drawText(QRectF(0,120,160,32),Qt.AlignmentFlag.AlignCenter,self.text())


def card():
    w=QFrame();w.setObjectName('Card');layout=QVBoxLayout(w);layout.setContentsMargins(24,22,24,22);layout.setSpacing(16);return w,layout

def spin(value,lo,hi,suffix='',decimals=2):
    w=QDoubleSpinBox();w.setRange(lo,hi);w.setDecimals(decimals);w.setValue(value);w.setSuffix(suffix);w.setMinimumWidth(120);w.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows);w.lineEdit().setTextMargins(9,0,4,0);return w

def numeric_input(value, lo, hi, suffix='', decimals=0, *, trim_zeros=False):
    """An editable numeric text field; arrows and wheel changes are disabled."""
    class NumberField(QDoubleSpinBox):
        def wheelEvent(self, event):
            event.ignore()
        def textFromValue(self, value):
            text=super().textFromValue(value)
            separator=self.locale().decimalPoint()
            return text.rstrip('0').rstrip(separator) if trim_zeros and separator in text else text
    field=NumberField();field.setRange(lo,hi);field.setDecimals(decimals)
    field.setValue(value);field.setSuffix(suffix);field.setMinimumWidth(120)
    # Qt's styled NoButtons layout collapses the editor to one pixel. Keep
    # the normal edit layout and remove both step controls with local styling.
    field.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
    field.setStyleSheet('QDoubleSpinBox::up-button,QDoubleSpinBox::down-button {width:0px; height:0px; border:none;} '
        'QDoubleSpinBox::up-arrow,QDoubleSpinBox::down-arrow {image:none; width:0px; height:0px;} '
        'QDoubleSpinBox QLineEdit {padding:0px; margin:0px; border:0px; min-height:0px; background:transparent;}')
    field.setKeyboardTracking(False);field.setReadOnly(False)
    field.lineEdit().setTextMargins(12,0,12,0);field.lineEdit().setReadOnly(False)
    return field

class EyeMark(QWidget):
    def __init__(self,size=64):super().__init__();self.setFixedSize(size,size)
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);s=self.width()
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#eeeeee'));p.drawRoundedRect(self.rect(),s*.24,s*.24)
        p.setPen(QPen(QColor('#242424'),s*.045));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(QRectF(s*.17,s*.30,s*.66,s*.4))
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#242424'));p.drawEllipse(QPointF(s*.5,s*.5),s*.095,s*.095)

class ImageBoard(QWidget):
    changed=pyqtSignal()
    def __init__(self):
        super().__init__();self.pix=QPixmap();self.image_path='';self.points=[];self.active=None;self.edit=True;self.message='';self.calibrating=False;self.setMinimumSize(360,240)
        self.target_style='dot';self.target_color='#35452e';self.target_remaining_s=None
        self.background_color='#f0f0f0'
        self.target_diameter_px=20.;self.countdown_font_px=14.;self.hint_font_px=22.;self.message_anchor='bottom';self.coordinate_frame='image_normalized'
    def rect_image(self):
        if self.pix.isNull():return self.rect()
        size=self.pix.size();size.scale(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        from PyQt6.QtCore import QRect
        return QRect((self.width()-size.width())//2,(self.height()-size.height())//2,size.width(),size.height())
    def load(self,path):
        if not path:self.pix=QPixmap();self.image_path='';self.update();return
        self.pix=QPixmap(str(path));self.image_path=str(Path(path).resolve())
        if self.pix.isNull():raise ValueError('无法读取图片，请选择PNG或JPG格式。')
        self.update()
    def point_pos(self,p):
        if self.calibrating or self.coordinate_frame=='screen_normalized':return p['x']*self.width(),p['y']*self.height()
        r=self.rect_image();return r.x()+p['x']*r.width(),r.y()+p['y']*r.height()
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor('#f0f0f0') if self.calibrating else QColor(self.background_color) if self.pix.isNull() else QColor('#151515'))
        if not self.pix.isNull() and not self.calibrating:p.drawPixmap(self.rect_image(),self.pix)
        show=range(len(self.points)) if self.edit else ([self.active] if self.active is not None else [])
        for i in show:
            x,y=self.point_pos(self.points[i])
            if not self.edit:
                draw_target(p,QPointF(x,y),style=self.target_style,color=self.target_color,remaining_s=self.target_remaining_s,diameter_px=self.target_diameter_px,countdown_font_px=self.countdown_font_px)
            else:
                p.setPen(QPen(QColor('#fff'),3));p.setBrush(QColor('#242424'));p.drawEllipse(QPointF(x,y),10,10)
            if self.edit:
                box=QRectF(x+14,y-23,30,25);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#fff'));p.drawRoundedRect(box,6,6)
                p.setPen(QColor('#242522'));p.drawText(box,Qt.AlignmentFlag.AlignCenter,str(i+1))
        if self.message:
            center=QPointF(*self.point_pos(self.points[self.active])) if self.active is not None and self.active<len(self.points) else None
            draw_hint(p,self.rect(),self.message,self.hint_font_px,center,self.target_diameter_px)
    def mousePressEvent(self,e):
        if not self.edit or self.pix.isNull():return
        r=self.rect_image()
        if e.button()==Qt.MouseButton.RightButton:
            if self.points:self.points.pop();self.changed.emit();self.update()
        elif r.contains(e.position().toPoint()):
            self.points.append(dict(x=(e.position().x()-r.x())/r.width(),y=(e.position().y()-r.y())/r.height(),seconds=10.));self.changed.emit();self.update()

class ScreenOriginBoard(ImageBoard):
    """Whole-screen preview, with image letterboxing inside the display bounds."""
    def __init__(self,screen_ratio):super().__init__();self.screen_ratio=screen_ratio
    def screen_rect(self):
        width=min(self.width(),self.height()*self.screen_ratio);height=width/self.screen_ratio
        from PyQt6.QtCore import QRect
        return QRect(round((self.width()-width)/2),round((self.height()-height)/2),round(width),round(height))
    def rect_image(self):
        r=self.screen_rect()
        if self.pix.isNull():return r
        size=self.pix.size();size.scale(r.size(),Qt.AspectRatioMode.KeepAspectRatio)
        from PyQt6.QtCore import QRect
        return QRect(r.x()+(r.width()-size.width())//2,r.y()+(r.height()-size.height())//2,size.width(),size.height())
    def point_pos(self,p):
        r=self.screen_rect();return r.x()+p['x']*r.width(),r.y()+p['y']*r.height()
    def mousePressEvent(self,event):
        r=self.screen_rect()
        if event.button()==Qt.MouseButton.RightButton:self.points=[dict(x=.5,y=.5,seconds=480.)]
        elif r.contains(event.position().toPoint()):self.points=[dict(x=(event.position().x()-r.x())/r.width(),y=(event.position().y()-r.y())/r.height(),seconds=480.)]
        else:return
        self.changed.emit();self.update()

class MarkerDialog(QDialog):
    def __init__(self,image,points=None,parent=None,*,origin=None,screen_ratio=None,title='设置原点',confirm_text='使用此原点',background_color='#f0f0f0'):
        super().__init__(parent);self.setWindowTitle(title);self.resize(980,710)
        layout=QVBoxLayout(self);layout.setContentsMargins(26,24,26,24);layout.setSpacing(16)
        install_chrome(self,layout)
        screen=QApplication.primaryScreen().geometry();ratio=screen_ratio or screen.width()/screen.height()
        self.board=ScreenOriginBoard(ratio)
        self.board.background_color=background_color
        if image:self.board.load(image)
        chosen=copy.deepcopy(origin or (points[0] if points and len(points)==1 else {'x':.5,'y':.5}))
        self.board.points=[dict(x=chosen['x'],y=chosen['y'],seconds=480.)];layout.addWidget(self.board,1)
        row=QHBoxLayout();row.addWidget(label('屏幕 X'));self.x=spin(chosen['x']*100,0,100,' %',1);row.addWidget(self.x)
        row.addWidget(label('屏幕 Y'));self.y=spin(chosen['y']*100,0,100,' %',1);row.addWidget(self.y);row.addStretch();row.addWidget(button('恢复中心',self.clear));layout.addLayout(row)
        self.board.changed.connect(self.refresh);self.x.valueChanged.connect(self.coordinates_changed);self.y.valueChanged.connect(self.coordinates_changed)
        foot=QHBoxLayout();foot.addStretch();foot.addWidget(button('取消',self.reject));foot.addWidget(button(confirm_text,self.accept,True));layout.addLayout(foot)
    def refresh(self):
        p=self.board.points[0]
        for control,value in [(self.x,p['x']*100),(self.y,p['y']*100)]:control.blockSignals(True);control.setValue(value);control.blockSignals(False)
        self.board.update()
    def coordinates_changed(self):self.board.points=[dict(x=self.x.value()/100,y=self.y.value()/100,seconds=480.)];self.board.update()
    def clear(self):self.board.points=[dict(x=.5,y=.5,seconds=480.)];self.refresh()
    def undo(self):self.clear()

class ScreenPointBoard(ScreenOriginBoard):
    """Editable whole-screen points; dragging never changes their dwell times."""
    selectedChanged=pyqtSignal(int)
    def __init__(self,screen_ratio):
        super().__init__(screen_ratio);self.selected=-1;self.dragging=False;self.allow_edit=True
    def nearest(self,position):
        if not self.points:return -1
        distances=[(QPointF(*self.point_pos(p))-position).manhattanLength() for p in self.points]
        index=min(range(len(distances)),key=distances.__getitem__)
        return index if distances[index]<28 else -1
    def mousePressEvent(self,event):
        r=self.screen_rect();index=self.nearest(event.position())
        if not self.allow_edit:
            if index>=0:self.selected=index;self.selectedChanged.emit(index)
            return
        if event.button()==Qt.MouseButton.RightButton:
            if index>=0:self.points.pop(index);self.selected=min(index,len(self.points)-1);self.changed.emit();self.update()
            return
        if event.button()!=Qt.MouseButton.LeftButton or not r.contains(event.position().toPoint()):return
        if index<0:
            self.points.append(dict(x=(event.position().x()-r.x())/r.width(),y=(event.position().y()-r.y())/r.height(),seconds=2.));index=len(self.points)-1;self.changed.emit()
        self.selected=index;self.dragging=True;self.selectedChanged.emit(index);self.update()
    def mouseMoveEvent(self,event):
        if not self.allow_edit or not self.dragging or self.selected<0:return
        r=self.screen_rect();point=self.points[self.selected];point['x']=max(0.,min(1.,(event.position().x()-r.x())/r.width()));point['y']=max(0.,min(1.,(event.position().y()-r.y())/r.height()));self.update()
    def mouseReleaseEvent(self,event):
        if self.dragging:self.dragging=False;self.changed.emit()

class TestPointsDialog(QDialog):
    def __init__(self,image,points,layout='custom',total_seconds=60.,parent=None,*,background_color='#f0f0f0',transition_exclusion_s=1.):
        super().__init__(parent);self.setWindowTitle('测试点');self.resize(940,790);self.point_layout=layout;self.total_seconds=total_seconds;self.transition_exclusion_s=float(transition_exclusion_s)
        outer=QVBoxLayout(self);outer.setContentsMargins(24,22,24,22);outer.setSpacing(14);install_chrome(self,outer)
        screen=QApplication.primaryScreen().geometry();self.board=ScreenPointBoard(screen.width()/screen.height());self.board.load(image);self.board.points=copy.deepcopy(points);self.board.allow_edit=layout=='custom';outer.addWidget(self.board,1)
        self.board.background_color=background_color
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['点','X（%）','Y（%）','时间（秒）']);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.table.setMaximumHeight(236);self.table.verticalHeader().hide();self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);outer.addWidget(self.table)
        self.board.changed.connect(self.refresh);self.board.selectedChanged.connect(self.table.selectRow);self.table.itemSelectionChanged.connect(self.select_row)
        row=QHBoxLayout();self.add_button=button('添加点',self.add_point);self.delete_button=button('删除点',self.delete_point);row.addWidget(self.add_button);row.addWidget(self.delete_button);row.addWidget(button('平均分配时长',self.average));row.addStretch();self.total=label();row.addWidget(self.total);outer.addLayout(row)
        self.add_button.setVisible(layout=='custom');self.delete_button.setVisible(layout=='custom')
        foot=QHBoxLayout();foot.addStretch();foot.addWidget(button('取消',self.reject));foot.addWidget(button('使用测试点',self.accept,True));outer.addLayout(foot);self.refresh()
    def refresh(self):
        selected=self.board.selected;self.table.setRowCount(len(self.board.points))
        for i,point in enumerate(self.board.points):
            self.table.setItem(i,0,QTableWidgetItem(str(i+1)))
            for column,key,maximum in [(1,'x',100),(2,'y',100),(3,'seconds',3600)]:
                control=spin(point[key]*100 if key in ('x','y') else point[key],.1 if key=='seconds' else 0,maximum,'',2)
                control.setEnabled(key=='seconds' or self.point_layout=='custom');control.valueChanged.connect(lambda value,row=i,field=key:self.set_value(row,field,value));self.table.setCellWidget(i,column,control)
        if 0<=selected<len(self.board.points):self.table.selectRow(selected)
        self.update_total();self.board.update()
    def set_value(self,index,key,value):
        self.board.points[index][key]=value/100 if key in ('x','y') else value;self.update_total();self.board.update()
    def update_total(self):self.total.setText(f"{len(self.board.points)} 点    {sum(p['seconds'] for p in self.board.points):g} 秒")
    def select_row(self):self.board.selected=self.table.currentRow();self.board.update()
    def add_point(self):self.board.points.append(dict(x=.5,y=.5,seconds=2.));self.board.selected=len(self.board.points)-1;self.refresh()
    def delete_point(self):
        i=self.table.currentRow()
        if 0<=i<len(self.board.points):self.board.points.pop(i);self.board.selected=min(i,len(self.board.points)-1);self.refresh()
    def average(self):
        if not self.board.points:return
        value=self.total_seconds/len(self.board.points)
        if value<=self.transition_exclusion_s:QMessageBox.warning(self,'时长不足','每点呈现时间必须大于注视稳定等待时间。');return
        for p in self.board.points:p['seconds']=value
        self.refresh()
    def accept(self):
        if not self.board.points:QMessageBox.warning(self,'测试点','请至少设置一个测试点。');return
        if any(p['seconds']<=self.transition_exclusion_s for p in self.board.points):
            QMessageBox.warning(self,'时长不足','每点呈现时间必须大于注视稳定等待时间。');return
        super().accept()

def pick_marker_color(color, parent, title='选择标记颜色'):
    from PyQt6.QtWidgets import QColorDialog

    dialog = QColorDialog(QColor(color), parent)
    dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
    dialog.setWindowTitle(title)
    install_chrome(dialog, dialog.layout(), resizable=False)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.selectedColor()
    return QColor()


class FixationSettingsDialog(MarkerDialog):
    """Edit a single optional fixation point independently of calibration targets."""

    def __init__(self, image, origin, style, color, diameter_px, parent=None, *, background_color='#f0f0f0'):
        super().__init__(image, parent=parent, origin=origin, title='标注点设置', confirm_text='应用标注点', background_color=background_color)
        self.color = QColor(color).name()
        self.style_combo = QComboBox()
        for title, value in [('十字', 'cross'), ('圆点', 'dot'), ('秒数倒计时', 'countdown')]:
            self.style_combo.addItem(title, value)
        self.style_combo.setCurrentIndex(self.style_combo.findData(style))
        self.diameter = spin(diameter_px, 4, 200, ' px', 1)
        self.color_button = button('选择颜色', self.choose_color)
        self.color_button.setAccessibleName('选择标注点颜色')

        form = QFormLayout()
        form.setSpacing(12)
        form.addRow('标记样式', self.style_combo)
        form.addRow('标记颜色', self.color_button)
        form.addRow('标记直径', self.diameter)
        index = self.layout().count() - 1
        self.layout().insertLayout(index, form)

        # ScreenOriginBoard continues to accept clicks. In display mode it
        # renders the actual stimulus instead of its numbered editing marker.
        self.board.edit = False
        self.board.active = 0
        self.style_combo.currentIndexChanged.connect(self.refresh_appearance)
        self.diameter.valueChanged.connect(self.refresh_appearance)
        self.refresh_appearance()

    def choose_color(self):
        color = pick_marker_color(self.color, self, '标注点颜色')
        if color.isValid():
            self.color = color.name()
            self.refresh_appearance()

    def refresh_appearance(self):
        swatch = QPixmap(16, 16)
        swatch.fill(QColor(self.color))
        self.color_button.setIcon(QIcon(swatch))
        self.color_button.setToolTip(self.color)
        self.board.target_style = self.style_combo.currentData()
        self.board.target_color = self.color
        self.board.target_diameter_px = self.diameter.value()
        self.board.target_remaining_s = 3.
        self.board.update()

    def values(self):
        return {
            'origin': {'x': self.x.value() / 100, 'y': self.y.value() / 100},
            'fixation_style': self.style_combo.currentData(),
            'fixation_color': self.color,
            'fixation_diameter_px': self.diameter.value(),
        }


class SettingsDialog(QDialog):
    def __init__(self,data,parent=None,*,role='settings',mode='experiment'):
        super().__init__(parent);self.data=normalize_settings(data,root=ROOT);self.index=-1;self.role=role;self.bank_mode='experiment'
        from settings_layout import SETTINGS_STYLE,group_form_rows,settings_navigation,settings_group
        title='方案设置' if role=='schemes' else '设置';self.setWindowTitle(title);self.resize(760 if role=='schemes' else 720,540 if role=='schemes' else 500)
        self.setMinimumSize(640,440)
        self.setObjectName('EyeSettings');self.setStyleSheet(SETTINGS_STYLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        base=QVBoxLayout(self);base.setContentsMargins(14,8,14,12);base.setSpacing(10);install_chrome(self,base)
        self.mode_tabs=QTabBar(self);self.mode_tabs.addTab(icon('target',size=20),'实验');self.mode_tabs.setExpanding(False)
        if not RAW_CAPTURE:
            self.mode_tabs.addTab(icon('chart',size=20),'测试')
        if role=='schemes' and not RAW_CAPTURE:base.addWidget(self.mode_tabs)
        else:self.mode_tabs.hide()
        tabs=QTabWidget();self.content_tabs=tabs;tabs.setTabBarAutoHide(True);content_row=QHBoxLayout();content_row.setSpacing(12);content_row.addWidget(tabs,1);base.addLayout(content_row,1);schemes_page=QWidget();tabs.addTab(schemes_page,icon('target',size=20),'实验');sl=QHBoxLayout(schemes_page);sl.setContentsMargins(0,0,0,0);sl.setSpacing(12)
        side=QVBoxLayout();side.setSpacing(8);self.list=QListWidget();self.list.setObjectName('SchemeList');self.list.setFixedWidth(120);side.addWidget(self.list,1)
        side.addWidget(button('新建',self.new_scheme));side.addWidget(button('复制',self.duplicate));side.addWidget(button('删除',self.delete));sl.addLayout(side)
        area=QScrollArea();self.scheme_scroll=area;area.setWidgetResizable(True);sl.addWidget(area,1);body=QWidget();body.setObjectName('SettingsPage');area.setWidget(body);form=QFormLayout(body);form.setSpacing(15)
        self.name=QLineEdit();self.calibration=QComboBox();self._loading_scheme=False;self._last_test_layout='5';self.test_points=[];self.test_points_by_layout={}
        for n in [0,5,9]:self.calibration.addItem('不校准' if n==0 else f'{n} 点校准',n)
        self.calibration_countdown=numeric_input(3,0,60,' 秒');self.calibration_countdown.setAccessibleName('校准前倒计时')
        self.calibration.currentIndexChanged.connect(lambda _:self.calibration_countdown.setEnabled(self.calibration.currentData()!=0))
        self.calibration_countdown.setEnabled(self.calibration.currentData()!=0)
        self.minutes=numeric_input(8,.05,1440,' 分钟',4,trim_zeros=True);self.minutes.setAccessibleName('总时长');self.model=QComboBox()
        self._duration_before_edit=8.;self._duration_dirty=False
        self.minutes.valueChanged.connect(self.duration_edited)
        self.minutes.lineEdit().textEdited.connect(self.duration_edited)
        if RAW_CAPTURE:
            self.model.setParent(self)
            self.model.addItem('', preferred_model())
            self.model.hide()
        else:
            for mid,text in MODEL_NAMES.items():
                if (ROOT/'models'/mid/'backbone.pt').exists():self.model.addItem(text,mid)
        form.addRow('名称',self.name);form.addRow('总时长',self.minutes)
        self.duration_label=form.labelForField(self.minutes)
        self.two_part_experiment=QCheckBox('双段实验（每段前后校准）')
        self.two_part_experiment.setAccessibleName('双段实验')
        form.addRow('实验流程',self.two_part_experiment)
        self.experiment_protocol_label=form.labelForField(self.two_part_experiment)
        self.rest_minutes=numeric_input(DEFAULT_REST_MINUTES,0,1440,' 分钟',4,trim_zeros=True)
        self.rest_minutes.setAccessibleName('休息时长')
        form.addRow('休息时长',self.rest_minutes)
        self.rest_minutes_label=form.labelForField(self.rest_minutes)
        self.two_part_experiment.toggled.connect(self.refresh_experiment_protocol)
        self.fixation_enabled=QCheckBox('启用单点注视')
        self.fixation_enabled.setToolTip('开启后在正式采集画面显示标注点。')
        self.fixation_button=button('标注点设置',self.mark)
        self.fixation_button.setIcon(icon('target',size=20))
        fixation_row=QHBoxLayout()
        fixation_row.setContentsMargins(0,0,0,0)
        fixation_row.addWidget(self.fixation_enabled)
        fixation_row.addWidget(self.fixation_button)
        fixation_row.addStretch()
        self.fixation_controls=QWidget()
        self.fixation_controls.setLayout(fixation_row)
        form.addRow('注视任务',self.fixation_controls)
        self.fixation_controls_label=form.labelForField(self.fixation_controls)
        self.fixation_enabled.toggled.connect(self.refresh_fixation_controls)
        form.addRow('个人校准',self.calibration)
        form.addRow('校准前倒计时',self.calibration_countdown)
        self.calibration_timing=QWidget();cal_form=QFormLayout(self.calibration_timing);cal_form.setContentsMargins(0,0,0,0);cal_form.setSpacing(10)
        self.calibration_dwell=numeric_input(2,.1,60,' 秒',2)
        self.calibration_exclude_start=numeric_input(500,0,60000,' ms')
        self.calibration_exclude_end=numeric_input(0,0,60000,' ms')
        for title,control in [('每点呈现时长',self.calibration_dwell),('开头排除时长',self.calibration_exclude_start),('末尾排除时长',self.calibration_exclude_end)]:
            control.setAccessibleName('校准'+title);cal_form.addRow(title,control)
        self.calibration_dwell.setToolTip('默认 2 秒；有效采样窗口为开头排除后至本点结束。')
        self.calibration_exclude_start.setToolTip('每个校准点出现后，此时间内的帧不参与校准。')
        self.calibration_exclude_end.setToolTip('每个校准点结束前，此时间内的帧不参与校准。')
        form.addRow('校准采样',self.calibration_timing)
        self.test_controls=QWidget();self.test_controls.setMinimumHeight(98);test_form=QVBoxLayout(self.test_controls);test_form.setContentsMargins(0,0,0,0);test_form.setSpacing(10)
        test_row=QHBoxLayout();self.test_layout=QComboBox()
        for title,value in [('默认5点','5'),('默认9点','9'),('自定义','custom')]:self.test_layout.addItem(title,value)
        test_row.addWidget(self.test_layout);test_row.addWidget(button('编辑点位与时长',self.edit_test_points));test_form.addLayout(test_row)
        self.transition_exclusion=numeric_input(1,0,60,' 秒',2);self.transition_exclusion.setAccessibleName('注视稳定等待时间')
        self.transition_exclusion.setToolTip('每个测试点出现后的这段时间不参与指标计算；首个点同样适用。')
        settling_form=QFormLayout();settling_form.setContentsMargins(0,0,0,0);settling_form.addRow('注视稳定等待时间',self.transition_exclusion);test_form.addLayout(settling_form)
        time_row=QHBoxLayout();time_row.addWidget(button('平均分配时长',self.average_point_times));self.test_points_summary=label();time_row.addWidget(self.test_points_summary);time_row.addStretch();test_form.addLayout(time_row);form.addRow('测试点',self.test_controls);self.test_controls_label=form.labelForField(self.test_controls)
        self.direction_controls=QWidget();direction_form=QFormLayout(self.direction_controls);direction_form.setContentsMargins(0,0,0,0);direction_form.setHorizontalSpacing(16);direction_form.setVerticalSpacing(10)
        self.direction_task_enabled=QCheckBox('启用方向反应任务')
        self.direction_task_enabled.setAccessibleName('启用方向反应任务')
        self.direction_task_enabled.setToolTip('每点随机显示一个方向，答错或未作答的点不参与分析。')
        direction_form.addRow(self.direction_task_enabled)
        self.direction_visible=numeric_input(.3,.05,5,' 秒',2)
        self.direction_visible.setAccessibleName('箭头显示时长')
        direction_form.addRow('箭头显示时长',self.direction_visible)
        self.direction_countdown=numeric_input(3,0,60,' 秒');self.direction_countdown.setAccessibleName('答后下一点倒计时')
        self.direction_countdown.setToolTip('作答后、下一测试点出现前的倒计时；设为 0 可立即继续。')
        direction_form.addRow('答后下一点倒计时',self.direction_countdown)
        form.addRow('方向任务',self.direction_controls);self.direction_controls_label=form.labelForField(self.direction_controls)
        self.direction_task_enabled.toggled.connect(self.direction_countdown.setEnabled)
        self.direction_task_enabled.toggled.connect(self.direction_visible.setEnabled)
        self.direction_countdown.setEnabled(False)
        self.direction_visible.setEnabled(False)
        self.test_layout.currentIndexChanged.connect(self.test_layout_changed)
        from target_visuals import TargetPreview
        self.target_style=QComboBox()
        for name,value in [('圆点','dot'),('十字','cross'),('秒数倒计时','countdown')]:self.target_style.addItem(name,value)
        self.target_style.setToolTip('用于个人校准点。' if RAW_CAPTURE else '用于个人校准点与测试任务点。')
        form.addRow('标记样式',self.target_style)
        self.target_style_label=form.labelForField(self.target_style)
        self.target_color='#35452e';self.target_color_button=button('选择颜色',self.choose_target_color)
        self.target_color_button.setAccessibleName('选择标记颜色')
        self.target_preview=TargetPreview();self.target_preview.setFixedSize(88,44)
        target_row=QHBoxLayout();target_row.setSpacing(8);target_row.addWidget(self.target_color_button);target_row.addWidget(self.target_preview);target_row.addStretch();form.addRow('标记颜色',target_row)
        self.target_color_label=form.labelForField(target_row)
        self.target_style.currentIndexChanged.connect(self.refresh_target_preview)
        self.visual_parameters={'target_diameter_px':20.,'countdown_font_px':14.,'hint_font_px':22.,'display_scale':1.}
        self.target_display_button=button('大小与屏幕预览',self.edit_target_display)
        form.addRow('标记显示',self.target_display_button)
        self.target_display_label=form.labelForField(self.target_display_button)
        if not RAW_CAPTURE:
            form.addRow('模型',self.model)
        self.background_mode=QComboBox();self.background_mode.setAccessibleName('背景类型')
        self.background_mode.addItem('纯色背景','solid');self.background_mode.addItem('图片背景','image')
        self.background_color='#f0f0f0';self.background_color_button=button('选择颜色',self.choose_background_color)
        self.background_color_button.setAccessibleName('选择背景颜色')
        self.background_preview=QLabel();self.background_preview.setFixedSize(64,38);self.background_preview.setAccessibleName('背景颜色预览')
        background_row=QHBoxLayout();background_row.setSpacing(8);background_row.addWidget(self.background_mode);background_row.addWidget(self.background_color_button);background_row.addWidget(self.background_preview);background_row.addStretch();form.insertRow(2,'背景',background_row)
        self.image_controls=QWidget();image_layout=QVBoxLayout(self.image_controls);image_layout.setContentsMargins(0,0,0,0);image_layout.setSpacing(10)
        self.image_info=label('尚未选择图片','Muted',True);image_layout.addWidget(self.image_info)
        ir=QHBoxLayout();ir.addWidget(button('选择图片',self.choose_image));ir.addStretch();image_layout.addLayout(ir)
        self.thumb=QLabel();self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter);self.thumb.setMinimumHeight(110);self.thumb.setStyleSheet('background:#efefef;border-radius:4px;');image_layout.addWidget(self.thumb);form.insertRow(3,'图片',self.image_controls)
        self.image_controls_label=form.labelForField(self.image_controls)
        self.background_mode.currentIndexChanged.connect(self.background_changed)
        self.origin_x=spin(50,0,100,' %',1);self.origin_y=spin(50,0,100,' %',1)
        # Keep the saved coordinate origin even when the stimulus is off.
        for control in (self.origin_x,self.origin_y):
            control.setParent(self)
            control.hide()
        self.fixation_parameters={}
        self.points_info=label();self.points_info.hide()
        self.assume=QCheckBox('将原点作为注视误差参考',self);self.assume.hide()
        # Keep legacy scheme values intact until a global processing choice is saved.
        self.cleanup = QCheckBox(self)
        self.cleanup.hide()
        self.maxfps=QCheckBox('按摄像头最大速度采集');form.addRow('',self.maxfps)
        self.scheme_sections=group_form_rows(form,[
            ('basic','基本设置',('名称','总时长','实验流程','休息时长','模型','注视任务','')),
            ('calibration','个人校准',('个人校准','校准前倒计时','校准采样')),
            ('task','注视任务',('测试点','方向任务')),
            ('appearance','标记与背景',('标记样式','标记颜色','标记显示','背景','图片'))])
        geometry_page=QWidget();geometry_page.setObjectName('SettingsPage');geometry_area=QScrollArea();geometry_area.setWidgetResizable(True);geometry_area.setWidget(geometry_page);tabs.addTab(geometry_area,icon('camera',size=20),'相机与屏幕');ga=QVBoxLayout(geometry_page);ga.setContentsMargins(0,0,4,0);ga.setSpacing(10)
        self.camera_page = geometry_area
        camera_group,gf=settings_group('摄像头与距离');ga.addWidget(camera_group);g=self.data['geometry']
        self.camera=QSpinBox();self.camera.setRange(0,20);self.camera.setMinimumWidth(130);self.camera.setValue(g['camera_index']);gf.addRow('摄像头编号',self.camera)
        self.camera_discovery_button=button('检测并选择摄像头',self.discover_cameras)
        gf.addRow('',self.camera_discovery_button)
        self.camera_check_button=button('检查摄像头',self.check_camera)
        gf.addRow('',self.camera_check_button)
        self.distance=spin(g.get('view_distance_cm',55)*10,150,2000,' mm',1);gf.addRow('眼睛到屏幕距离',self.distance)
        self.distance_mode=QComboBox();self.distance_mode.addItem('使用填写距离','fixed');self.distance_mode.addItem('逐帧估计距离','estimated');self.distance_mode.setCurrentIndex(max(0,self.distance_mode.findData(g.get('distance_mode','estimated'))));gf.addRow('距离方式',self.distance_mode)
        self.intrinsics=copy.deepcopy(g.get('intrinsics',{}));self.intrinsics_label=label('已标定' if self.intrinsics else '尚未标定','Muted')
        self.camera.valueChanged.connect(self.camera_changed)
        intr=QHBoxLayout();intr.setSpacing(8);self.calibrate_camera_button=button('相机标定',self.calibrate_camera);intr.addWidget(self.calibrate_camera_button);intr.addWidget(button('导入参数',self.import_intrinsics));intr.addWidget(self.intrinsics_label);intr.addStretch();gf.addRow('内参',intr)
        self.camera_profile={key:g[key] for key in ['camera_width','camera_height'] if key in g}
        self.advanced=QWidget();af=QFormLayout(self.advanced);af.setContentsMargins(0,0,0,0);af.setSpacing(8)
        self.sw=spin(g['screen_width_cm']*10,50,2000,' mm');self.sh=spin(g['screen_height_cm']*10,50,2000,' mm');af.addRow('显示区域宽',self.sw);af.addRow('显示区域高',self.sh)
        self.center=QCheckBox('摄像头在屏幕上方居中');self.center.setChecked(g['camera_top_center']);af.addRow('',self.center)
        self.cx=spin(g['camera_x_cm']*10,-500,500,' mm');self.gap=spin(g['camera_gap_cm']*10,-500,500,' mm');af.addRow('相机向右偏移',self.cx);af.addRow('相机高于屏幕上沿',self.gap)
        self.center.toggled.connect(lambda checked:self.cx.setEnabled(not checked));self.cx.setEnabled(not self.center.isChecked())
        self.fov=spin(g['fov_degrees'],20,130,' °',1);af.addRow('无内参时水平视场角',self.fov);af.addRow('',button('清除已导入内参',self.clear_intrinsics,quiet=True));screen_group,screen_form=settings_group('屏幕与相机位置');screen_form.addRow(self.advanced);ga.addWidget(screen_group);ga.addStretch()
        foot=QHBoxLayout();foot.addStretch();foot.addWidget(button('取消',self.reject));foot.addWidget(button('保存',self.save,True));base.addLayout(foot)
        if role=='schemes':tabs.removeTab(1)
        else:
            tabs.removeTab(0)
            self.processing_page = self.build_raw_output_page() if RAW_CAPTURE else self.build_processing_page()
            tabs.insertTab(0, self.processing_page, icon('folder', size=20), '保存位置' if RAW_CAPTURE else '数据处理')
            tabs.setCurrentWidget(self.processing_page)
            navigation,self.settings_nav_buttons=settings_navigation(tabs)
            self.settings_navigation=navigation
            content_row.insertWidget(0,navigation)
        tabs.tabBar().hide()
        self.list.currentRowChanged.connect(self.switch_scheme)
        self.mode_tabs.currentChanged.connect(self.switch_mode)
        self.calibration.currentIndexChanged.connect(self.refresh_fixation_controls)
        self.mode_tabs.setCurrentIndex(1 if mode=='test' and not RAW_CAPTURE else 0)
        self.switch_mode(self.mode_tabs.currentIndex())

    def showEvent(self,event):
        super().showEvent(event)
        from settings_layout import fit_settings_window
        fit_settings_window(self)

    def build_raw_output_page(self):
        page = QWidget()
        page.setObjectName('SettingsPage')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        output, output_layout = card()
        output_layout.addWidget(label('实验保存位置', 'CardTitle'))
        current = processing_for_mode(self.data, 'experiment') or {}
        directory = current.get('output_dir') or str(DATA_ROOT / 'sessions')
        self.processing_output_dir = QLineEdit(directory)
        self.processing_output_dir.setAccessibleName('实验保存位置')
        self.processing_folder_button = button('选择文件夹', self.choose_processing_folder)
        row = QHBoxLayout()
        row.addWidget(self.processing_output_dir, 1)
        row.addWidget(self.processing_folder_button)
        output_layout.addLayout(row)
        layout.addWidget(output)
        layout.addStretch()
        return page

    def build_processing_page(self):
        page = QScrollArea()
        page.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName('SettingsPage')
        page.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)
        self._processing_drafts = {}
        for mode, bank, selected in (('experiment', 'schemes', 'selected_scheme'),
                                     ('test', 'test_schemes', 'selected_test_scheme')):
            policy = processing_for_mode(self.data, mode)
            if policy is None:
                scheme = next(s for s in self.data[bank] if s['id'] == self.data[selected])
                policy = dict(mode='immediate',
                              retain_raw_rgb=not scheme.get('delete_raw_after_analysis', False),
                              output_dir='')
            if mode == 'test':
                policy['mode'] = 'immediate'
            self._processing_drafts[mode] = policy
        self.processing_initial = copy.deepcopy(self._processing_drafts)
        self._processing_scope = 'experiment'
        current = self._processing_drafts[self._processing_scope]

        mode_card, mode_layout = card()
        self.processing_mode = QComboBox()
        self.processing_mode.setAccessibleName('数据处理方式')
        self.processing_mode.addItem('结束后自动处理', 'immediate')
        self.processing_mode.addItem('保存后处理', 'deferred')
        self.processing_mode.setToolTip('保存后处理会保存校准素材和实验视频，可交给独立的数据处理程序。')
        self.processing_mode.setCurrentIndex(self.processing_mode.findData(current['mode']))
        mode_layout.addWidget(label('处理方式', 'CardTitle'))
        mode_layout.addWidget(self.processing_mode)
        layout.addWidget(mode_card)

        self.processing_device_card, device_layout = card()
        device_layout.addWidget(label('处理设备', 'CardTitle'))
        device_form = QFormLayout()
        device_form.setHorizontalSpacing(12)
        device_form.setVerticalSpacing(8)
        self.processing_device = QComboBox()
        self.processing_device.setAccessibleName('自动处理设备')
        self.processing_device.setMinimumWidth(0)
        self.processing_device.setMinimumContentsLength(10)
        self.processing_device.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for title, device in [('自动（优先 GPU）', 'auto'), ('CPU', 'cpu'), ('NVIDIA GPU', 'cuda')]:
            self.processing_device.addItem(title, device)
        selected_device = current.get('device', 'auto')
        if self.processing_device.findData(selected_device) < 0:
            self.processing_device.addItem('GPU ' + selected_device.split(':')[-1], selected_device)
        self.processing_device.setCurrentIndex(self.processing_device.findData(selected_device))
        device_form.addRow('设备', self.processing_device)
        self.processing_cpu_threads = QSpinBox()
        self.processing_cpu_threads.setAccessibleName('自动处理 CPU 线程数')
        cores = max(1, os.cpu_count() or 1)
        self.processing_cpu_threads.setRange(1, cores)
        self.processing_cpu_threads.setValue(current.get('cpu_threads', min(8, cores)))
        device_form.addRow('CPU 线程', self.processing_cpu_threads)
        device_layout.addLayout(device_form)
        self.processing_hardware_status = label(f'CPU：{cores} 个逻辑核\nGPU：正在检测', wrap=True)
        self.processing_hardware_status.setTextFormat(Qt.TextFormat.PlainText)
        device_layout.addWidget(self.processing_hardware_status)
        layout.addWidget(self.processing_device_card)
        self.processing_hardware = None
        self._processing_probe_started = False
        self._processing_runtime_dirty = set()
        self.processing_device.currentIndexChanged.connect(self.processing_device_changed)
        self.processing_cpu_threads.valueChanged.connect(lambda *_: self._processing_runtime_dirty.add('cpu_threads'))
        self.processing_cpu_threads.lineEdit().textEdited.connect(lambda *_: self._processing_runtime_dirty.add('cpu_threads'))

        self.retain_card, retain_layout = card()
        retain_layout.addWidget(label('原始 RGB', 'CardTitle'))
        self.retain_raw_rgb = QCheckBox('保留原始 RGB 视频')
        self.retain_raw_rgb.setChecked(current['retain_raw_rgb'])
        retain_layout.addWidget(self.retain_raw_rgb)
        self.retain_raw_rgb.setToolTip('取消勾选后，整次分析完成并校验结果后清理原始视频。')
        layout.addWidget(self.retain_card)

        self.output_card, output_layout = card()
        output_layout.addWidget(label('实验保存位置', 'CardTitle'))
        output_row = QHBoxLayout()
        output_row.setSpacing(10)
        self.processing_output_dir = QLineEdit(current['output_dir'])
        self.processing_output_dir.setAccessibleName('实验保存位置')
        self.processing_output_dir.setPlaceholderText('选择或输入文件夹的绝对路径')
        self.processing_folder_button = button('选择文件夹', self.choose_processing_folder)
        self.processing_folder_button.setAccessibleName('选择实验保存位置')
        output_row.addWidget(self.processing_output_dir, 1)
        output_row.addWidget(self.processing_folder_button)
        output_layout.addLayout(output_row)
        layout.addWidget(self.output_card)
        for section in (mode_layout, device_layout, retain_layout, output_layout):
            section.setContentsMargins(14, 12, 14, 12)
            section.setSpacing(8)
        layout.addStretch()
        self.processing_mode.currentIndexChanged.connect(self.refresh_processing_controls)
        self.refresh_processing_controls()
        return page

    def processing_values(self):
        result=dict(mode='immediate' if self._processing_scope == 'test' else self.processing_mode.currentData(),
                    retain_raw_rgb=self.retain_raw_rgb.isChecked(),output_dir=self.processing_output_dir.text().strip())
        self.processing_cpu_threads.interpretText()
        for key, value in (('device',self.processing_device.currentData()), ('cpu_threads',self.processing_cpu_threads.value())):
            if key in self._processing_runtime_dirty:
                result[key]=value
            elif key in self.processing_initial['experiment']:
                result[key]=self.processing_initial['experiment'][key]
        return result

    def refresh_processing_controls(self):
        immediate = self.processing_mode.currentData() == 'immediate'
        self.retain_card.setVisible(immediate)
        self.processing_device_card.setVisible(immediate)
        self.output_card.setVisible(self._processing_scope == 'experiment'
                                   and self.processing_mode.currentData() == 'deferred')
        self.refresh_processing_device_status()
        if immediate:
            QTimer.singleShot(0,self.start_processing_hardware_probe)

    def processing_device_changed(self):
        self._processing_runtime_dirty.add('device')
        self.refresh_processing_device_status()

    def start_processing_hardware_probe(self):
        if self._processing_probe_started or self.processing_mode.currentData() != 'immediate':
            return
        self._processing_probe_started=True
        task=_HardwareProbe()
        _PROCESSING_PROBES.add(task)
        task.signals.ready.connect(self.show_processing_hardware)
        task.signals.finished.connect(lambda item=task:_PROCESSING_PROBES.discard(item))
        task.signals.finished.connect(task.signals.deleteLater)
        QThreadPool.globalInstance().start(task)

    def show_processing_hardware(self, hardware):
        self.processing_hardware=dict(hardware)
        cores=max(1,int(hardware.get('cpu_count') or os.cpu_count() or 1))
        blocked=self.processing_cpu_threads.blockSignals(True)
        self.processing_cpu_threads.setMaximum(cores)
        if 'cpu_threads' not in self.processing_initial['experiment'] and 'cpu_threads' not in self._processing_runtime_dirty:
            self.processing_cpu_threads.setValue(min(8,cores))
        self.processing_cpu_threads.blockSignals(blocked)
        selected=self.processing_device.currentData()
        blocked=self.processing_device.blockSignals(True)
        for entry in hardware.get('cuda_devices') or []:
            device=entry.get('device') or f'cuda:{entry["index"]}'
            title=f'GPU {device.split(":")[-1]}：{entry.get("name",device)}'
            index=self.processing_device.findData(device)
            if index<0:
                self.processing_device.addItem(title,device)
            else:
                self.processing_device.setItemText(index,title)
        self.processing_device.setCurrentIndex(self.processing_device.findData(selected))
        self.processing_device.blockSignals(blocked)
        self.refresh_processing_device_status()

    def refresh_processing_device_status(self):
        device=self.processing_device.currentData() or 'auto'
        self.processing_cpu_threads.setEnabled(not device.startswith('cuda'))
        hardware=self.processing_hardware
        if hardware is None:
            return
        cores=max(1,int(hardware.get('cpu_count') or os.cpu_count() or 1))
        if hardware.get('cuda_available'):
            names='；'.join(str(entry.get('name') or entry.get('device')) for entry in hardware.get('cuda_devices') or [])
            gpu='GPU：'+names
            if device.startswith('cuda'):
                index=int(device.split(':')[-1]) if ':' in device else 0
                if index>=len(hardware.get('cuda_devices') or []):
                    gpu=f'GPU {index} 不可用，当前可用 {len(hardware.get("cuda_devices") or [])} 张'
        else:
            gpu='GPU 不可用：'+str(hardware.get('cuda_reason') or '未检测到可用的 NVIDIA CUDA 设备')
        self.processing_hardware_status.setText(f'CPU：{cores} 个逻辑核\n'+gpu)

    def validate_processing_device(self, policy):
        device=policy.get('device','auto')
        if policy['mode']!='immediate' or not device.startswith('cuda'):
            return
        hardware=self.processing_hardware
        if hardware is None:
            raise ValueError('正在检测 GPU，请稍候再保存，或选择自动/CPU。')
        if not hardware.get('cuda_available'):
            raise ValueError('无法使用 GPU：'+str(hardware.get('cuda_reason') or '未检测到可用的 NVIDIA CUDA 设备'))
        index=int(device.split(':')[-1]) if ':' in device else 0
        if index>=len(hardware.get('cuda_devices') or []):
            raise ValueError(f'无法使用 GPU {index}，当前可用 {len(hardware.get("cuda_devices") or [])} 张显卡')

    def choose_processing_folder(self):
        start = Path(self.processing_output_dir.text())
        directory = QFileDialog.getExistingDirectory(self, '选择实验保存位置',
                                                     str(start if start.is_absolute() and start.is_dir() else Path.home()))
        if directory:
            self.processing_output_dir.setText(directory)

    def apply_processing_settings(self):
        if RAW_CAPTURE:
            from tempfile import TemporaryFile
            policy = validate_processing(dict(mode='deferred', retain_raw_rgb=True,
                                              output_dir=self.processing_output_dir.text().strip()))
            try:
                with TemporaryFile(dir=policy['output_dir']):
                    pass
            except OSError as error:
                raise ValueError('实验保存位置无法写入，请选择其他文件夹。') from error
            self.data['processing_by_mode'] = {'experiment': policy}
            return
        self._processing_drafts[self._processing_scope] = self.processing_values()
        policies = copy.deepcopy(self._processing_drafts)
        for mode, values in policies.items():
            if values['mode'] == 'immediate':
                values['output_dir'] = self.processing_initial[mode]['output_dir']
        editing_processing = self.content_tabs.currentWidget() is self.processing_page
        if policies == self.processing_initial and not editing_processing:
            return
        for mode, policy in policies.items():
            values = validate_processing(policy)
            changed = values != self.processing_initial[mode]
            active = editing_processing and mode == self._processing_scope
            if mode == 'experiment' and (changed or active):
                self.validate_processing_device(values)
            if values['mode'] == 'deferred' and (changed or active):
                from tempfile import TemporaryFile
                title = '实验' if mode == 'experiment' else '测试'
                directory = Path(values['output_dir'])
                if not directory.is_dir():
                    raise ValueError(f'{title}的原始 RGB 保存文件夹不存在，请重新选择')
                try:
                    with TemporaryFile(dir=directory):
                        pass
                except OSError as exc:
                    raise ValueError(f'{title}的原始 RGB 保存文件夹无法写入，请选择其他位置') from exc
            policies[mode] = values
        self.data['processing_by_mode'] = policies

    @property
    def bank_key(self):return 'test_schemes' if self.bank_mode=='test' else 'schemes'
    @property
    def selected_key(self):return 'selected_test_scheme' if self.bank_mode=='test' else 'selected_scheme'
    def switch_mode(self,index):
        self.flush();self.bank_mode='test' if index==1 and not RAW_CAPTURE else 'experiment';self.index=-1
        self.refresh_list(self.data.get(self.selected_key))
    def duration_edited(self,*_):
        if not self._loading_scheme:self._duration_dirty=True
    def reset_origin(self):self.origin_x.setValue(50);self.origin_y.setValue(50)
    def refresh_fixation_controls(self):
        self.calibration_timing.setEnabled(self.calibration.currentData()!=0)
        formal=self.role=='schemes' and self.bank_mode=='experiment'
        self.scheme_sections['task'].setVisible(self.bank_mode=='test')
        self.fixation_controls.setVisible(formal)
        self.fixation_controls_label.setVisible(formal)
        self.fixation_button.setVisible(formal and self.fixation_enabled.isChecked())
        self.target_style_label.setText('校准样式' if formal else '标记样式')
        self.target_color_label.setText('校准颜色' if formal else '标记颜色')
        self.target_display_label.setText('校准显示' if formal else '标记显示')
        appearance_enabled=not formal or self.calibration.currentData()!=0
        for control in (self.target_style,self.target_color_button,self.target_preview,self.target_display_button):
            control.setEnabled(appearance_enabled)
    def refresh_target_preview(self):
        color=QColor(self.target_color)
        if not color.isValid():color=QColor('#35452e')
        self.target_color=color.name()
        chip=QPixmap(16,16);chip.fill(color);self.target_color_button.setIcon(QIcon(chip));self.target_color_button.setToolTip(self.target_color)
        self.target_preview.set_target(self.target_style.currentData() or 'dot',self.target_color)
        if hasattr(self,'background_mode'):
            formal=self.bank_mode=='experiment'
            self.target_preview.set_background('#f0f0f0' if formal else self.background_color,
                None if formal else self.current_background_image())
        if hasattr(self,'visual_parameters'):
            from target_visuals import display_dimensions
            diameter,countdown,_=display_dimensions(self.visual_parameters);self.target_preview.set_dimensions(diameter,countdown)
    def edit_target_display(self):
        from target_preview import TargetDisplayDialog
        scheme=self.data[self.bank_key][self.index];origin=dict(x=self.origin_x.value()/100,y=self.origin_y.value()/100)
        formal=self.bank_mode=='experiment'
        dialog=TargetDisplayDialog(self.visual_parameters,self.target_style.currentData(),self.target_color,
            None if formal else self.current_background_image(),origin,self,
            background_color='#f0f0f0' if formal else self.background_color)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.visual_parameters=dict(dialog.values);self.refresh_target_preview()
    def choose_target_color(self):
        color=pick_marker_color(self.target_color,self)
        if color.isValid():self.target_color=color.name();self.refresh_target_preview()
    def test_layout_changed(self):
        if self._loading_scheme:return
        from scheme_store import preset_points
        if self.test_points:self.test_points_by_layout[self._last_test_layout]=copy.deepcopy(self.test_points)
        layout=self.test_layout.currentData() or '5';self._last_test_layout=layout
        self.test_points=copy.deepcopy(self.test_points_by_layout.get(layout,[]))
        if not self.test_points and layout!='custom':self.test_points=preset_points(layout)
        self.refresh_test_points()
    def refresh_test_points(self):
        self.test_points_summary.setText(f"{len(self.test_points)} 点    {sum(p['seconds'] for p in self.test_points):g} 秒")
        if self.test_points and self.bank_mode=='test':
            duration=sum(p['seconds'] for p in self.test_points)/60
            loading=self._loading_scheme;self._loading_scheme=True
            self.minutes.setValue(duration);self._loading_scheme=loading
            if not loading:self._duration_before_edit=duration;self._duration_dirty=False
    def average_point_times(self):
        if not self.test_points:
            QMessageBox.information(self,'测试点','请先添加测试点。');return
        self.minutes.interpretText();self.transition_exclusion.interpretText()
        minutes=self.minutes.value() if self._duration_dirty else self._duration_before_edit
        seconds=minutes*60/len(self.test_points)
        if seconds<=self.transition_exclusion.value():QMessageBox.warning(self,'时长不足','每点呈现时间必须大于注视稳定等待时间。');return
        for point in self.test_points:point['seconds']=seconds
        self.refresh_test_points()
    def edit_test_points(self):
        scheme=self.data[self.bank_key][self.index]
        self.minutes.interpretText();self.transition_exclusion.interpretText()
        minutes=self.minutes.value() if self._duration_dirty else self._duration_before_edit
        dialog=TestPointsDialog(self.current_background_image(),self.test_points,self.test_layout.currentData(),minutes*60,self,
            background_color=self.background_color,transition_exclusion_s=self.transition_exclusion.value())
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.test_points=copy.deepcopy(dialog.board.points);self.refresh_test_points()
    def refresh_list(self,selected_id=None):
        self.list.blockSignals(True);self.list.clear()
        for s in self.data[self.bank_key]:self.list.addItem(s['name'])
        index=next((i for i,s in enumerate(self.data[self.bank_key]) if s['id']==selected_id),0);self.list.blockSignals(False);self.index=-1;self.list.setCurrentRow(index)
    def flush(self):
        if self.index<0 or self.role!='schemes':return
        for control in (self.minutes,self.rest_minutes,self.calibration_countdown,self.calibration_dwell,self.calibration_exclude_start,self.calibration_exclude_end,self.transition_exclusion,self.direction_countdown,self.direction_visible):control.interpretText()
        if self.bank_mode=='test' and self._duration_dirty and self.test_points:
            seconds=self.minutes.value()*60/len(self.test_points)
            for point in self.test_points:point['seconds']=seconds
            self.refresh_test_points()
        s=self.data[self.bank_key][self.index];origin=dict(x=self.origin_x.value()/100,y=self.origin_y.value()/100)
        self.test_points_by_layout[self.test_layout.currentData()]=copy.deepcopy(self.test_points)
        points=copy.deepcopy(self.test_points) if self.bank_mode=='test' else [dict(origin,seconds=self.minutes.value()*60)]
        duration=sum(p['seconds'] for p in points)/60 if self.bank_mode=='test' else (self.minutes.value() if self._duration_dirty else self._duration_before_edit)
        if self.bank_mode=='experiment':points=[dict(origin,seconds=duration*60)]
        s.update(name=self.name.text().strip(),mode=self.bank_mode,test_layout=self.test_layout.currentData(),test_points_by_layout=copy.deepcopy(self.test_points_by_layout),calibration_points=self.calibration.currentData(),calibration_countdown_s=self.calibration_countdown.value(),duration_minutes=duration,model_id=self.model.currentData(),assume_fixation=self.bank_mode=='test',max_fps=self.maxfps.isChecked(),origin=origin,points=points,delete_raw_after_analysis=self.cleanup.isChecked(),target_style=self.target_style.currentData(),target_color=self.target_color)
        s.update(calibration_dwell_s=self.calibration_dwell.value(),calibration_exclude_start_ms=self.calibration_exclude_start.value(),calibration_exclude_end_ms=self.calibration_exclude_end.value(),transition_exclusion_s=self.transition_exclusion.value())
        s.update(direction_task_enabled=self.bank_mode=='test' and self.direction_task_enabled.isChecked(),direction_countdown_s=self.direction_countdown.value(),direction_visible_s=self.direction_visible.value())
        s.update(self.visual_parameters)
        s.update(background_mode=self.background_mode.currentData(),background_color=self.background_color)
        if self.bank_mode=='experiment':
            s.update(self.fixation_parameters,fixation_enabled=self.fixation_enabled.isChecked())
            s.update(experiment_protocol='two_part' if self.two_part_experiment.isChecked() else 'single',
                     rest_minutes=self.rest_minutes.value())
        if s['name']:self.list.item(self.index).setText(s['name'])
        self.data[self.selected_key]=s['id']
    def switch_scheme(self,index):
        self.flush();self.index=index
        if index<0:return
        self._loading_scheme=True
        self.minutes.setMinimum(.01 if self.bank_mode=='test' else .05)
        s=self.data[self.bank_key][index];self.name.setText(s['name']);self.calibration.setCurrentIndex(self.calibration.findData(s['calibration_points']));self.minutes.setValue(s['duration_minutes']);self.model.setCurrentIndex(self.model.findData(s['model_id']));self.assume.setChecked(s['assume_fixation']);self.maxfps.setChecked(s['max_fps']);self.cleanup.setChecked(s.get('delete_raw_after_analysis',False))
        self.two_part_experiment.setChecked(self.bank_mode=='experiment' and s.get('experiment_protocol')=='two_part')
        self.rest_minutes.setValue(s.get('rest_minutes',DEFAULT_REST_MINUTES))
        self.refresh_experiment_protocol()
        self.calibration_countdown.setValue(s.get('calibration_countdown_s',3));self.calibration_countdown.setEnabled(self.calibration.currentData()!=0)
        self.calibration_dwell.setValue(s.get('calibration_dwell_s',2.));self.calibration_exclude_start.setValue(s.get('calibration_exclude_start_ms',500));self.calibration_exclude_end.setValue(s.get('calibration_exclude_end_ms',0));self.transition_exclusion.setValue(s.get('transition_exclusion_s',1.))
        direction_enabled=self.bank_mode=='test' and s.get('direction_task_enabled',False)
        self.direction_task_enabled.setChecked(direction_enabled);self.direction_countdown.setValue(s.get('direction_countdown_s',3));self.direction_countdown.setEnabled(direction_enabled)
        self.direction_visible.setValue(s.get('direction_visible_s',.3));self.direction_visible.setEnabled(direction_enabled)
        layout=s.get('test_layout','custom' if len(s.get('points',[]))>1 else '5');self.test_layout.setCurrentIndex(max(0,self.test_layout.findData(layout)));self._last_test_layout=layout
        self.test_points_by_layout=copy.deepcopy(s.get('test_points_by_layout',{}));self.test_points=copy.deepcopy(s.get('points',[])) if self.bank_mode=='test' else copy.deepcopy(self.test_points_by_layout.get(layout,[]))
        if not self.test_points and layout!='custom':self.test_points=preset_points(layout)
        is_test=self.bank_mode=='test'
        self.test_controls.setVisible(is_test);self.test_controls_label.setVisible(is_test);self.direction_controls.setVisible(is_test);self.direction_controls_label.setVisible(is_test);self.refresh_test_points();self._duration_before_edit=s['duration_minutes'];self._duration_dirty=False;self._loading_scheme=False
        self.visual_parameters={key:float(s.get(key,default)) for key,default in [('target_diameter_px',20.),('countdown_font_px',14.),('hint_font_px',22.),('display_scale',1.)]};self.visual_parameters['hint_font_px']=max(22.,self.visual_parameters['hint_font_px'])
        self.target_style.setCurrentIndex(max(0,self.target_style.findData(s.get('target_style','dot'))));self.target_color=s.get('target_color','#000000' if self.bank_mode=='experiment' else '#35452e');self.refresh_target_preview()
        from scheme_store import DEFAULT_FIXATION
        self.fixation_parameters={key:s.get(key,default) for key,default in DEFAULT_FIXATION.items() if key!='fixation_enabled'}
        self.fixation_enabled.setChecked(s.get('fixation_enabled',False))
        self.refresh_fixation_controls()
        self.background_mode.blockSignals(True);self.background_mode.setCurrentIndex(max(0,self.background_mode.findData(s.get('background_mode','solid'))));self.background_mode.blockSignals(False)
        self.background_color=s.get('background_color','#f0f0f0');self.refresh_background_controls();self.refresh_target_preview()
        origin=s.get('origin',s['points'][0] if len(s.get('points',[]))==1 else dict(x=.5,y=.5));self.origin_x.setValue(origin['x']*100);self.origin_y.setValue(origin['y']*100);self.refresh_image()
    def refresh_experiment_protocol(self):
        formal = self.bank_mode == 'experiment'
        paired = formal and self.two_part_experiment.isChecked()
        self.two_part_experiment.setVisible(formal)
        self.experiment_protocol_label.setVisible(formal)
        self.rest_minutes.setVisible(paired)
        self.rest_minutes_label.setVisible(paired)
        duration_label = '每段注视时长' if paired else '总时长'
        self.duration_label.setText(duration_label)
        self.minutes.setAccessibleName(duration_label)
        zero_calibration = self.calibration.findData(0)
        self.calibration.model().item(zero_calibration).setEnabled(not paired)
        if paired and self.calibration.currentData()==0:
            self.calibration.setCurrentIndex(self.calibration.findData(9))
    def current_background_image(self):
        if self.index<0 or self.background_mode.currentData()!='image':return None
        scheme=dict(self.data[self.bank_key][self.index],background_mode='image')
        return resolve_image(scheme)
    def refresh_background_controls(self):
        solid=self.background_mode.currentData()=='solid'
        self.background_color_button.setVisible(solid);self.background_preview.setVisible(solid)
        self.image_controls.setVisible(not solid);self.image_controls_label.setVisible(not solid)
        self.background_preview.setStyleSheet(f'background:{self.background_color};border:1px solid #d8d8d8;border-radius:6px;')
        self.background_preview.setToolTip(self.background_color);self.background_color_button.setToolTip(self.background_color)
    def background_changed(self):
        self.refresh_background_controls()
        if self.index>=0:self.refresh_image();self.refresh_target_preview()
    def choose_background_color(self):
        color=pick_marker_color(self.background_color,self,'选择背景颜色')
        if color.isValid():self.background_color=color.name();self.background_changed()
    def refresh_image(self):
        path=self.current_background_image();self.image_info.setText(path.name if path else '尚未选择图片')
        pix=QPixmap(str(path)) if path else QPixmap()
        if not pix.isNull():self.thumb.setPixmap(pix.scaled(460,120,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        else:self.thumb.clear();self.thumb.setText('可选择图片作为实验背景')
    def choose_image(self):
        path,_=QFileDialog.getOpenFileName(self,'选择实验图片','','图片 (*.png *.jpg *.jpeg *.bmp)')
        if path:
            try:self.data[self.bank_key][self.index]['image']=import_image(path);self.background_mode.setCurrentIndex(self.background_mode.findData('image'));self.refresh_image();self.refresh_target_preview()
            except Exception as error:QMessageBox.warning(self,'无法选择',str(error))
    def mark(self):
        if self.index<0 or self.bank_mode!='experiment' or not self.fixation_enabled.isChecked():
            return
        scheme=self.data[self.bank_key][self.index]
        origin=dict(x=self.origin_x.value()/100,y=self.origin_y.value()/100)
        editor=FixationSettingsDialog(self.current_background_image(),origin,
            self.fixation_parameters['fixation_style'],self.fixation_parameters['fixation_color'],
            self.fixation_parameters['fixation_diameter_px'],self,background_color=self.background_color)
        if editor.exec()==QDialog.DialogCode.Accepted:
            values=editor.values()
            point=values.pop('origin')
            self.origin_x.setValue(point['x']*100)
            self.origin_y.setValue(point['y']*100)
            self.fixation_parameters=values
    def new_scheme(self):
        self.flush();s=default_scheme(f'方案 {len(self.data[self.bank_key])+1}',mode=self.bank_mode);s['model_id']=preferred_model(ROOT)
        self.data[self.bank_key].append(s);self.refresh_list(s['id'])
    def duplicate(self):
        import uuid
        self.flush();s=copy.deepcopy(self.data[self.bank_key][self.index]);s['id']=uuid.uuid4().hex;s['name']+=' 副本';self.data[self.bank_key].append(s);self.refresh_list(s['id'])
    def delete(self):
        if len(self.data[self.bank_key])==1:QMessageBox.information(self,'实验方案','至少保留一个方案。');return
        if QMessageBox.question(self,'删除方案','删除当前方案？已采集的数据保留。')==QMessageBox.StandardButton.Yes:self.data[self.bank_key].pop(self.index);self.index=-1;self.refresh_list()
    def import_intrinsics(self):
        import json,numpy as np
        path,_=QFileDialog.getOpenFileName(self,'导入相机内参','','JSON (*.json)')
        if path:
            try:
                value=json.loads(Path(path).read_text(encoding='utf8'));matrix=np.asarray(value['camera_matrix'])
                if matrix.shape!=(3,3) or not np.isfinite(matrix).all() or matrix[0,0]<=0 or matrix[1,1]<=0 or len(value['intrinsics_resolution'])!=2:raise ValueError('内参格式无效')
                self.intrinsics=value;self.intrinsics_label.setText('已导入')
            except Exception as error:QMessageBox.warning(self,'内参错误',str(error))
    def clear_intrinsics(self):self.intrinsics={};self.intrinsics_label.setText('尚未标定')
    def camera_changed(self, index):
        if self.intrinsics:
            self.intrinsics={}
            self.intrinsics_label.setText('摄像头已更换，请重新标定或导入')

    def discover_cameras(self):
        from camera_discovery import CameraDiscoveryDialog
        config = dict(camera_index=self.camera.value(), **self.camera_profile)
        dialog = CameraDiscoveryDialog(config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.camera.setValue(dialog.selected_index)

    def check_camera(self):
        from camera_check import CameraCheckDialog
        config = dict(camera_index=self.camera.value(), **self.camera_profile)
        CameraCheckDialog(config, self).exec()

    def calibrate_camera(self):
        from camera_calibration_ui import CameraCalibrationDialog
        size=(self.camera_profile.get('camera_width',1280),self.camera_profile.get('camera_height',720));dialog=CameraCalibrationDialog(self.camera.value(),size,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.camera.setValue(dialog.intrinsics_result['camera_index']);self.intrinsics=copy.deepcopy(dialog.intrinsics_result)
            w,h=dialog.intrinsics_result['intrinsics_resolution'];self.camera_profile=dict(camera_width=w,camera_height=h);self.intrinsics_label.setText(f'已标定 {w}×{h}')
    def save(self):
        try:
            self.flush()
            if self.role=='settings':
                self.apply_processing_settings()
                geometry=copy.deepcopy(self.data['geometry'])
                geometry.update(screen_width_cm=self.sw.value()/10,screen_height_cm=self.sh.value()/10,camera_top_center=self.center.isChecked(),camera_x_cm=0 if self.center.isChecked() else self.cx.value()/10,camera_gap_cm=self.gap.value()/10,fov_degrees=self.fov.value(),camera_index=self.camera.value(),intrinsics=self.intrinsics,view_distance_cm=self.distance.value()/10,distance_mode=self.distance_mode.currentData())
                geometry.update(self.camera_profile);self.data['geometry']=geometry
            else:
                for key in ('schemes','test_schemes'):
                    for s in self.data[key]:validate_scheme(s,require_image=False)
            save_settings(self.data);self.accept()
        except Exception as error:QMessageBox.warning(self,'设置未保存',str(error))

class SchemeDialog(SettingsDialog):
    def __init__(self,data,parent=None,mode='experiment'):
        super().__init__(data,parent,role='schemes',mode=mode)

class TestStartDialog(QDialog):
    __test__=False
    def __init__(self,data,parent=None):
        super().__init__(parent);self.data=normalize_settings(data,root=ROOT);self.setWindowTitle('测试');self.setMinimumWidth(400)
        layout=QVBoxLayout(self);layout.setContentsMargins(28,24,28,24);layout.setSpacing(18);install_chrome(self,layout,resizable=False)
        layout.addWidget(label('测试方案'))
        self.scheme_combo=QComboBox();self.scheme_combo.setAccessibleName('测试方案')
        for scheme in self.data['test_schemes']:self.scheme_combo.addItem(scheme['name'],scheme['id'])
        self.scheme_combo.setCurrentIndex(max(0,self.scheme_combo.findData(self.data['selected_test_scheme'])))
        layout.addWidget(self.scheme_combo);self.summary=label('',None,True);layout.addWidget(self.summary)
        self.scheme_combo.currentIndexChanged.connect(self.refresh_summary);self.refresh_summary()
        actions=QHBoxLayout();actions.addStretch();self.cancel_button=button('取消',self.reject);self.start_button=button('开始',self.accept,True)
        self.cancel_button.setAutoDefault(False);self.start_button.setDefault(True)
        actions.addWidget(self.cancel_button);actions.addWidget(self.start_button);layout.addLayout(actions)
    def selected_scheme_id(self):return self.scheme_combo.currentData()
    def refresh_summary(self):
        scheme=next(s for s in self.data['test_schemes'] if s['id']==self.selected_scheme_id())
        points=scheme['calibration_points'];calibration=f'{points}点校准' if points else '不校准'
        processing = '结束后自动处理'
        self.summary.setText(f'{scheme["duration_minutes"]:g} 分钟    {calibration}\n{processing}')

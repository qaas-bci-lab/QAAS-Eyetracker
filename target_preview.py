"""Continuous wheel + numeric controls and an unscaled full-screen stimulus preview."""
from PyQt6.QtCore import Qt,QPointF,QRectF,pyqtSignal
from PyQt6.QtGui import QPainter,QColor,QPen,QFont,QPixmap,QShortcut,QKeySequence
from PyQt6.QtWidgets import QWidget,QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QDoubleSpinBox,QApplication,QSlider
from window_chrome import install_chrome
from target_visuals import draw_target,draw_hint,display_dimensions

class ContinuousWheel(QWidget):
    delta=pyqtSignal(float)
    def __init__(self,parent=None):
        super().__init__(parent);self.setFixedSize(156,38);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setCursor(Qt.CursorShape.SizeHorCursor);self.setAccessibleName('连续调节滚轮');self.offset=0.;self.drag=None
        self.setToolTip('滚轮或左右拖动调整；按住Alt可精调。')
    def move_delta(self,amount):self.offset+=amount;self.delta.emit(amount);self.update()
    def wheelEvent(self,e):
        amount=e.pixelDelta().y()/30 if not e.pixelDelta().isNull() else e.angleDelta().y()/120
        if e.modifiers() & Qt.KeyboardModifier.AltModifier:amount*=.1
        self.move_delta(amount);e.accept()
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton:self.drag=e.position().x();e.accept()
    def mouseMoveEvent(self,e):
        if self.drag is not None:
            amount=(e.position().x()-self.drag)/10;self.drag=e.position().x()
            if e.modifiers() & Qt.KeyboardModifier.AltModifier:amount*=.1
            self.move_delta(amount);e.accept()
    def mouseReleaseEvent(self,e):self.drag=None;e.accept()
    def keyPressEvent(self,e):
        if e.key() in [Qt.Key.Key_Left,Qt.Key.Key_Right]:self.move_delta(-.1 if e.key()==Qt.Key.Key_Left else .1);e.accept()
        else:super().keyPressEvent(e)
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(QPen(QColor('#d8d8d8'),1));p.setBrush(QColor('#f2f2f2'));p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),8,8)
        for i in range(-12,13):
            x=self.width()/2+(i+self.offset%1)*10
            if 8<x<self.width()-8:p.setPen(QPen(QColor('#909090'),1));p.drawLine(QPointF(x,11),QPointF(x,27))
        p.setPen(QPen(QColor('#242424'),2));p.drawLine(QPointF(self.width()/2,7),QPointF(self.width()/2,31))

class DimensionRow(QWidget):
    changed=pyqtSignal(float)
    def __init__(self,value,low,high,parent=None):
        super().__init__(parent);row=QHBoxLayout(self);row.setContentsMargins(0,0,0,0);self.wheel=ContinuousWheel();self.number=QDoubleSpinBox();self.number.setDecimals(1);self.number.setRange(low,high);self.number.setSingleStep(.1);self.number.setSuffix(' px');self.number.setMinimumWidth(132);self.number.setValue(value)
        self._precise_value=float(value);self._wheel_updating=False
        row.addWidget(self.wheel);row.addWidget(self.number);self.wheel.delta.connect(self.nudge);self.number.valueChanged.connect(self.number_changed)
    def nudge(self,step):
        self._precise_value=max(self.number.minimum(),min(self.number.maximum(),self._precise_value+step*.5));self._wheel_updating=True
        try:self.number.setValue(self._precise_value)
        finally:self._wheel_updating=False
    def number_changed(self,value):
        if not self._wheel_updating:self._precise_value=value
        self.changed.emit(value)
    def value(self):return self.number.value()

class SmoothSlider(QSlider):
    def __init__(self,parent=None):
        super().__init__(Qt.Orientation.Horizontal,parent);self.setRange(250,4000);self.setSingleStep(1);self.setPageStep(100);self._wheel_remainder=0.
        self.setMinimumWidth(210);self.setMinimumHeight(26);self.setAccessibleName('标记与字体统一缩放');self._dragging=False
        self.setStyleSheet('QSlider::groove:horizontal{height:5px;background:#e4e4e4;border-radius:2px;} QSlider::sub-page:horizontal{background:#242424;border-radius:2px;} QSlider::handle:horizontal{width:14px;height:14px;margin:-5px 0;background:#ffffff;border:2px solid #242424;border-radius:8px;}')
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);y=self.height()/2;x=9+(self.value()-self.minimum())/(self.maximum()-self.minimum())*max(1,self.width()-18)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#e4e4e4'));p.drawRoundedRect(QRectF(9,y-2.5,max(1,self.width()-18),5),2.5,2.5);p.setBrush(QColor('#242424'));p.drawRoundedRect(QRectF(9,y-2.5,max(0,x-9),5),2.5,2.5)
        p.setPen(QPen(QColor('#242424'),2));p.setBrush(QColor('#ffffff'));p.drawEllipse(QPointF(x,y),7,7)
        if self.hasFocus():p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor('#b8b8b8'),1));p.drawEllipse(QPointF(x,y),10,10)
    def position_value(self,position):self.setValue(round(self.minimum()+max(0.,min(1.,(position.x()-9)/max(1,self.width()-18)))*(self.maximum()-self.minimum())))
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self._dragging=True;self.setFocus();self.position_value(event.position());event.accept()
        else:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self._dragging:self.position_value(event.position());event.accept()
        else:super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):self._dragging=False;event.accept()
    def wheelEvent(self,event):
        step=(event.pixelDelta().y()/3 if not event.pixelDelta().isNull() else event.angleDelta().y()/12)
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:step*=.1
        self._wheel_remainder+=step;whole=int(self._wheel_remainder);self._wheel_remainder-=whole
        if whole:self.setValue(self.value()+whole)
        event.accept()

class ScaleRow(QWidget):
    changed=pyqtSignal(float)
    def __init__(self,value,parent=None):
        super().__init__(parent);layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(16)
        self.slider=SmoothSlider();self.number=QDoubleSpinBox();self.number.setRange(25,400);self.number.setDecimals(1);self.number.setSuffix(' %');self.number.setSingleStep(.1);self.number.setFixedWidth(130)
        layout.addWidget(self.slider,1);layout.addWidget(self.number);self.slider.valueChanged.connect(self.slider_changed);self.number.valueChanged.connect(self.number_changed);self.set_ratio(value)
    def set_ratio(self,value):self.number.setValue(float(value)*100)
    def slider_changed(self,value):self.number.setValue(value/10)
    def number_changed(self,value):
        self.slider.blockSignals(True);self.slider.setValue(round(value*10));self.slider.blockSignals(False);self.changed.emit(value/100)
    def value(self):return self.number.value()/100

class ScreenPreview(QDialog):
    def __init__(self,owner,image,origin,*,background_color=None):
        super().__init__(owner);self.owner=owner;self.image=QPixmap(str(image)) if image else QPixmap();self.origin=origin;self.controls_visible=True
        self.background_color=background_color or getattr(owner,'background_color','#f0f0f0')
        self.setProperty('plain_fullscreen',True)
        self.setWindowTitle('屏幕实际大小预览');self.setGeometry(QApplication.primaryScreen().geometry())
        self.panel=QWidget(self);self.panel.setStyleSheet('background:#ffffff;border:1px solid #e4e4e4;border-radius:10px;')
        form=QFormLayout(self.panel);form.setContentsMargins(18,15,18,15);self.values={}
        for key,title,low,high in owner.definitions:
            row=DimensionRow(owner.values[key],low,high);row.changed.connect(lambda v,k=key:self.change_value(k,v));form.addRow(title,row);self.values[key]=row
            row.hide();form.labelForField(row).hide()
        self.scale_row=ScaleRow(owner.values.get('display_scale',1.));self.scale_row.changed.connect(lambda v:self.change_value('display_scale',v));form.addRow('显示大小',self.scale_row)
        done=QPushButton('完成预览');done.clicked.connect(self.accept);form.addRow(done)
        self.hide_shortcut=QShortcut(QKeySequence('Tab'),self);self.hide_shortcut.activated.connect(self.toggle_panel)
    def toggle_panel(self):self.controls_visible=not self.controls_visible;self.panel.setVisible(self.controls_visible)
    def change_value(self,key,value):
        if key=='display_scale':self.owner.rows[key].set_ratio(value)
        else:self.owner.rows[key].number.setValue(value)
        self.update()
    def resizeEvent(self,e):
        super().resizeEvent(e);self.panel.adjustSize()
        x=20 if self.origin['x']>=.5 else max(20,self.width()-self.panel.width()-20)
        bottom_margin=max(64,display_dimensions(self.owner.values)[2]*2.4+40)
        y=20 if self.origin['y']>=.35 else max(20,self.height()-self.panel.height()-bottom_margin)
        self.panel.move(round(x),round(y))
    def wheelEvent(self,e):
        # Scrolling the preview itself adjusts the diameter with fractional input.
        self.scale_row.slider.wheelEvent(e)
    def keyPressEvent(self,e):
        if e.key()==Qt.Key.Key_Tab:self.toggle_panel();e.accept()
        else:super().keyPressEvent(e)
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor(self.background_color) if self.image.isNull() else QColor('#151515'))
        if not self.image.isNull():
            size=self.image.size();size.scale(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
            p.drawPixmap((self.width()-size.width())//2,(self.height()-size.height())//2,self.image.scaled(size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        diameter,countdown,hint=display_dimensions(self.owner.values);draw_target(p,QPointF(self.origin['x']*self.width(),self.origin['y']*self.height()),self.owner.style,self.owner.color,3.,diameter_px=diameter,countdown_font_px=countdown)
        draw_hint(p,self.rect(),'按 S 开始',hint,QPointF(self.origin['x']*self.width(),self.origin['y']*self.height()),diameter)

class TargetDisplayDialog(QDialog):
    definitions=[('target_diameter_px','标记直径',4.,200.),('countdown_font_px','倒计时字号',6.,96.),('hint_font_px','提示字号',8.,96.)]
    def __init__(self,parameters,style='dot',color='#35452e',image=None,origin=None,parent=None,*,background_color='#f0f0f0'):
        super().__init__(parent);self.values=dict(parameters);self.values.setdefault('display_scale',1.);self.values['hint_font_px']=max(22.,self.values.get('hint_font_px',22.));self.style=style;self.color=color;self.image=image;self.origin=origin or dict(x=.5,y=.5);self.rows={};self.setWindowTitle('标记显示');self.resize(590,340)
        self.background_color=background_color
        layout=QVBoxLayout(self);layout.setContentsMargins(26,24,26,24);layout.setSpacing(17)
        install_chrome(self,layout)
        form=QFormLayout();form.setSpacing(14)
        for key,title,low,high in self.definitions:
            row=DimensionRow(self.values[key],low,high,self);row.hide();self.rows[key]=row;row.changed.connect(lambda value,k=key:self.update_value(k,value))
        self.rows['countdown_font_px'].setToolTip('多位秒数会在标记内自动适配，保持外部直径不变。')
        self.scale_row=ScaleRow(self.values['display_scale']);self.rows['display_scale']=self.scale_row;self.scale_row.changed.connect(lambda v:self.update_value('display_scale',v));form.addRow('显示大小',self.scale_row);layout.addLayout(form)
        from target_visuals import TargetPreview
        self.preview=TargetPreview();self.preview.setMinimumHeight(100);self.preview.set_target(style,color);self.preview.set_background(background_color,image);layout.addWidget(self.preview)
        self.size_info=QLabel(self);self.size_info.hide()
        buttons=QHBoxLayout();full=QPushButton('屏幕实际大小预览');full.clicked.connect(self.full_preview);buttons.addWidget(full);buttons.addStretch();cancel=QPushButton('取消');cancel.clicked.connect(self.reject);buttons.addWidget(cancel);save=QPushButton('使用此设置');save.setProperty('primary',True);save.clicked.connect(self.accept);buttons.addWidget(save);layout.addLayout(buttons);self.refresh()
    def update_value(self,key,value):self.values[key]=float(value);self.refresh()
    def refresh(self):
        diameter,countdown,_=display_dimensions(self.values);self.preview.set_dimensions(diameter,countdown)
    def full_preview(self):
        preview=ScreenPreview(self,self.image,self.origin);preview.showFullScreen();preview.exec();self.refresh()

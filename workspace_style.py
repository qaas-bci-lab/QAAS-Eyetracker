"""Neutral monochrome surfaces for both desktop applications."""
STYLE = '''
QWidget { font-size:14px; color:#272727; }
QMainWindow,QDialog,QWidget#Workspace { background:#F5F5F5; }
QLabel#Title { font-size:28px; font-weight:650; color:#272727; }
QLabel#HeroTitle { font-size:20px; font-weight:650; color:#272727; }
QLabel#CardTitle,QLabel#SectionTitle { font-size:18px; font-weight:650; color:#272727; }
QLabel#Muted { color:#666666; font-size:14px; }
QLabel#FieldGroupTitle { color:#666666; font-size:14px; font-weight:550; }
QLabel#SchemeBadge { font-size:14px; padding:6px 10px; background:#EAEAEA; border:0; border-radius:7px; color:#333333; }
QWidget#PersonWorkspace { background:#FFFFFF; border-radius:16px; }
QFrame#LaunchCard { background:#FFFFFF; border:1px solid #DDDDDD; border-radius:18px; }
QWidget#ModeSwitch { background:#F5F5F5; border-radius:12px; }
QPushButton { min-height:34px; padding:0 12px; border-radius:9px; border:1px solid #B8B8B8; background:#FFFFFF; color:#272727; }
QPushButton:hover:enabled { border-color:#888888; background:#EEEEEE; }
QPushButton:pressed:enabled { border-color:#666666; background:#DADADA; }
QPushButton:focus { border:2px solid #252525; }
QPushButton:disabled { background:#F1F1F1; border-color:#D5D5D5; color:#999999; }
QPushButton[primary="true"] { background:#252525; border:1px solid #252525; color:#FFFFFF; }
QPushButton[primary="true"]:hover:enabled { background:#444444; border-color:#444444; }
QPushButton[primary="true"]:pressed:enabled { background:#080808; border-color:#080808; }
QPushButton[primary="true"]:disabled { background:#D2D2D2; border-color:#C5C5C5; color:#777777; }
QPushButton#LaunchButton { min-height:40px; border-radius:10px; font-size:15px; font-weight:600; }
QPushButton[quiet="true"] { background:#F9F9F9; border:1px solid #C8C8C8; color:#555555; }
QPushButton[quiet="true"]:hover:enabled { background:#EEEEEE; border-color:#888888; color:#272727; }
QPushButton[quiet="true"]:pressed:enabled { background:#DADADA; border-color:#666666; color:#272727; }
QPushButton[quiet="true"]:disabled { background:#F1F1F1; border-color:#D5D5D5; color:#999999; }
QPushButton[segment="true"] { background:#F5F5F5; border:1px solid #C8C8C8; min-height:32px; border-radius:8px; color:#666666; }
QPushButton[segment="true"]:checked { background:#FFFFFF; color:#252525; border:1px solid #888888; font-weight:650; }
QPushButton[segment="true"]:focus { border:2px solid #252525; }
QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox { min-height:34px; border-radius:9px; background:#FFFFFF; border:1px solid #DDDDDD; color:#272727; selection-background-color:#EAEAEA; selection-color:#272727; }
QSpinBox,QDoubleSpinBox { padding-left:12px; padding-right:28px; }
QComboBox:hover,QLineEdit:hover,QSpinBox:hover,QDoubleSpinBox:hover { border-color:#AAAAAA; background:#FFFFFF; }
QComboBox:focus,QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus { border:2px solid #252525; }
QComboBox:disabled,QLineEdit:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled { background:#F5F5F5; color:#999999; border-color:#DDDDDD; }
QComboBox QAbstractItemView { background:#FFFFFF; color:#272727; selection-background-color:#EAEAEA; selection-color:#272727; border:1px solid #DDDDDD; }
QListWidget { background:transparent; border:0; padding:4px; outline:none; }
QListWidget::item { min-height:40px; padding:8px 12px; border-radius:10px; }
QListWidget::item:selected { background:#EAEAEA; color:#333333; }
QListWidget::item:hover { background:#F5F5F5; }
QFrame#Card { background:#FFFFFF; border:1px solid #DDDDDD; border-radius:16px; }
QFrame#Divider { background:#DDDDDD; }
QFrame#SettingsNavigation { background:#F5F5F5; border:1px solid #DDDDDD; border-radius:14px; }
QPushButton#SettingsNav { text-align:left; padding:0 14px; background:transparent; border:1px solid transparent; border-radius:10px; color:#666666; }
QPushButton#SettingsNav:checked { background:#FFFFFF; border-color:#888888; color:#252525; font-weight:600; }
QPushButton#SettingsNav:hover:!checked { background:#EAEAEA; border-color:#B8B8B8; }
QPushButton#SettingsNav:pressed { background:#DADADA; border-color:#666666; }
QTableWidget { background:#FFFFFF; border:0; border-radius:0; gridline-color:#DDDDDD; selection-background-color:#EAEAEA; selection-color:#272727; }
QHeaderView::section { padding:12px 8px; background:#F5F5F5; color:#666666; border:0; font-size:14px; font-weight:550; }
QTableWidget::item { padding:8px; }
QProgressBar { background:#DDDDDD; }
QProgressBar::chunk { background:#252525; }
QTabBar::tab { color:#666666; padding:10px 20px; }
QTabBar::tab:selected { color:#252525; border-bottom:2px solid #252525; }
QScrollBar::handle:vertical { background:#BBBBBB; }
QScrollBar::handle:vertical:hover { background:#999999; }
QToolTip { color:#272727; background:#FFFFFF; border:1px solid #DDDDDD; padding:7px; }
QSplitter::handle { background:transparent; }
'''


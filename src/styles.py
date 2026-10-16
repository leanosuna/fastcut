"""
Theme and styles for FastCut.
"""

DARK_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #1a1c23;
    color: #e0e4ec;
}

QWidget {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 13px;
    color: #d8dce6;
}

QMenuBar {
    background-color: #14161c;
    color: #cfd4e0;
    border-bottom: 1px solid #282c37;
    padding: 2px;
}

QMenuBar::item {
    background-color: transparent;
    padding: 4px 10px;
    border-radius: 3px;
}

QMenuBar::item:selected {
    background-color: #2b313f;
    color: #ffffff;
}

QMenu {
    background-color: #1e222b;
    color: #e0e4ec;
    border: 1px solid #323846;
    padding: 4px;
}

QMenu::item {
    padding: 6px 24px 6px 16px;
    border-radius: 3px;
}

QMenu::item:selected {
    background-color: #3b82f6;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background: #2e3442;
    margin: 4px 8px;
}

QToolBar {
    background-color: #16181f;
    border-bottom: 1px solid #282c37;
    spacing: 8px;
    padding: 4px 8px;
}

QPushButton {
    background-color: #262a35;
    color: #e0e4ec;
    border: 1px solid #363c4c;
    border-radius: 4px;
    padding: 5px 12px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #313745;
    border-color: #485065;
}

QPushButton:pressed {
    background-color: #1e212a;
}

QPushButton:disabled {
    background-color: #1b1d24;
    color: #5d6373;
    border-color: #252832;
}

QComboBox {
    background-color: #21242d;
    color: #e0e4ec;
    border: 1px solid #363c4c;
    border-radius: 4px;
    padding: 5px 10px;
}

QComboBox:hover {
    border-color: #485065;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #363c4c;
}

QComboBox QAbstractItemView {
    background-color: #1e222b;
    color: #e0e4ec;
    border: 1px solid #363c4c;
    selection-background-color: #3b82f6;
}

QSlider::groove:horizontal {
    border: 1px solid #2e3340;
    height: 4px;
    background: #252934;
    border-radius: 2px;
}

QSlider::sub-page:horizontal {
    background: #3b82f6;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    width: 14px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #60a5fa;
}

QStatusBar {
    background-color: #14161c;
    border-top: 1px solid #282c37;
    color: #9aa1b2;
}

QSplitter::handle {
    background-color: #232732;
}

QSplitter::handle:hover {
    background-color: #3b82f6;
}

QGroupBox {
    border: 1px solid #2d3340;
    border-radius: 5px;
    margin-top: 8px;
    padding-top: 12px;
    font-weight: bold;
    color: #b8c0d1;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
}
"""

"""
CANtroller - Intelligent CAN Bus Tool
A simple CAN bus viewer with automatic response capabilities
"""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

from main_window import MainWindow


# Dark theme stylesheet
DARK_STYLE = """
QMainWindow {
    background-color: #2b2b2b;
}

QWidget {
    background-color: #2b2b2b;
    color: #e0e0e0;
    font-size: 12px;
}

QGroupBox {
    border: 1px solid #555;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: bold;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #4fc3f7;
}

QTableWidget {
    background-color: #1e1e1e;
    alternate-background-color: #252525;
    gridline-color: #444;
    border: 1px solid #555;
    border-radius: 3px;
}

QTableWidget::item {
    padding: 5px;
}

QTableWidget::item:selected {
    background-color: #0078d4;
}

QHeaderView::section {
    background-color: #383838;
    color: #e0e0e0;
    padding: 5px;
    border: 1px solid #555;
    font-weight: bold;
}

QToolBar {
    background-color: #383838;
    border: none;
    padding: 5px;
    spacing: 5px;
}

QPushButton {
    background-color: #0078d4;
    color: white;
    border: none;
    padding: 8px 16px;
    border-radius: 4px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #1084d8;
}

QPushButton:pressed {
    background-color: #006cbd;
}

QPushButton:disabled {
    background-color: #555;
    color: #888;
}

QPushButton:checked {
    background-color: #4CAF50;
}

QComboBox {
    background-color: #383838;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px 10px;
    min-width: 120px;
}

QComboBox:hover {
    border-color: #0078d4;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #383838;
    selection-background-color: #0078d4;
    border: 1px solid #555;
}

QStatusBar {
    background-color: #007acc;
    color: white;
}

QLabel {
    background-color: transparent;
}

QLineEdit, QSpinBox {
    background-color: #383838;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 5px;
    color: #e0e0e0;
}

QLineEdit:focus, QSpinBox:focus {
    border-color: #0078d4;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #555;
    border-radius: 3px;
    background-color: #383838;
}

QCheckBox::indicator:checked {
    background-color: #0078d4;
    border-color: #0078d4;
}

QDialog {
    background-color: #2b2b2b;
}

QDialogButtonBox QPushButton {
    min-width: 80px;
}

QMenuBar {
    background-color: #383838;
    color: #e0e0e0;
}

QMenuBar::item:selected {
    background-color: #0078d4;
}

QMenu {
    background-color: #383838;
    border: 1px solid #555;
}

QMenu::item:selected {
    background-color: #0078d4;
}

QSplitter::handle {
    background-color: #555;
}

QSplitter::handle:vertical {
    height: 4px;
}
"""


def main():
    app = QApplication(sys.argv)
    
    # Apply dark theme
    app.setStyleSheet(DARK_STYLE)
    
    # Set application font
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    # Create and show main window
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

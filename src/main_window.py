"""
Main Window - CANtroller application interface
"""
import time
import re
import json
import os
import sys
import csv
from datetime import datetime
from typing import Dict, Optional, List
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QToolBar, QStatusBar, QLabel,
    QComboBox, QPushButton, QGroupBox, QHeaderView, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QCheckBox, QSpinBox, QDialogButtonBox,
    QMenu, QMenuBar, QTabWidget, QFrame, QFileDialog, QApplication
)
from PyQt6.QtCore import Qt, QTimer, QMimeData
from PyQt6.QtGui import QAction, QIcon, QColor, QFont, QDragEnterEvent, QDropEvent
import can

from can_manager import CANManager, ResponseRule, TransmitMessage

# Settings file path - use app directory for PyInstaller compatibility
def get_settings_path():
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        app_dir = os.path.dirname(sys.executable)
    else:
        # Running from source
        app_dir = os.path.dirname(__file__)
    return os.path.join(app_dir, 'settings.json')

SETTINGS_FILE = get_settings_path()


class HexDataLineEdit(QLineEdit):
    """Custom QLineEdit that auto-formats hex data with spaces"""
    
    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.textChanged.connect(self._on_text_changed)
        self._updating = False
    
    def _on_text_changed(self, text: str):
        if self._updating:
            return
        
        self._updating = True
        
        # Remove all spaces and non-hex characters
        clean = re.sub(r'[^0-9A-Fa-f]', '', text)
        
        # Split into pairs and join with spaces
        pairs = [clean[i:i+2] for i in range(0, len(clean), 2)]
        formatted = ' '.join(pairs).upper()
        
        # Preserve cursor position
        cursor_pos = self.cursorPosition()
        old_len = len(text)
        
        self.setText(formatted)
        
        # Adjust cursor position
        new_len = len(formatted)
        if new_len > old_len:
            self.setCursorPosition(cursor_pos + (new_len - old_len))
        else:
            self.setCursorPosition(min(cursor_pos, new_len))
        
        self._updating = False


class HexByteLineEdit(QLineEdit):
    """Custom QLineEdit for single byte hex input with auto-advance"""
    
    def __init__(self, next_edit=None, parent=None):
        super().__init__("00", parent)
        self.setMaximumWidth(35)
        self.setMaxLength(2)
        self.next_edit = next_edit
        self.textChanged.connect(self._on_text_changed)
        self._updating = False
    
    def set_next_edit(self, next_edit):
        self.next_edit = next_edit
    
    def _on_text_changed(self, text: str):
        if self._updating:
            return
        
        self._updating = True
        
        # Keep only hex characters
        clean = re.sub(r'[^0-9A-Fa-f]', '', text).upper()
        self.setText(clean)
        
        # Auto-advance to next field when 2 chars entered
        if len(clean) == 2 and self.next_edit and self.next_edit.isEnabled():
            self.next_edit.setFocus()
            self.next_edit.selectAll()
        
        self._updating = False


class AddRuleDialog(QDialog):
    """Dialog for adding/editing response rules"""
    
    def __init__(self, parent=None, rule: Optional[ResponseRule] = None):
        super().__init__(parent)
        self.setWindowTitle("Add Response Rule" if rule is None else "Edit Response Rule")
        self.setMinimumWidth(400)
        
        layout = QFormLayout(self)
        
        # Trigger ID
        self.trigger_id_edit = QLineEdit()
        self.trigger_id_edit.setPlaceholderText("e.g., 18900240")
        layout.addRow("Trigger ID (hex):", self.trigger_id_edit)
        
        # Response ID
        self.response_id_edit = QLineEdit()
        self.response_id_edit.setPlaceholderText("e.g., 18904002")
        layout.addRow("Response ID (hex):", self.response_id_edit)
        
        # Response Data with auto-spacing
        self.response_data_edit = HexDataLineEdit("e.g., 03 E8 00 64 00 32 00 00")
        layout.addRow("Response Data (hex):", self.response_data_edit)
        
        # Extended ID checkbox
        self.extended_check = QCheckBox("Extended ID (29-bit)")
        self.extended_check.setChecked(True)
        layout.addRow("", self.extended_check)
        
        # Delay
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 10000)
        self.delay_spin.setSuffix(" ms")
        layout.addRow("Response Delay:", self.delay_spin)
        
        # Comment
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("e.g., BMS_Response")
        layout.addRow("Comment:", self.comment_edit)
        
        # Buttons
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        
        # Fill in existing rule data if editing
        if rule:
            self.trigger_id_edit.setText(f"{rule.trigger_id:X}")
            self.response_id_edit.setText(f"{rule.response_id:X}")
            self.response_data_edit.setText(" ".join(f"{b:02X}" for b in rule.response_data))
            self.extended_check.setChecked(rule.is_extended)
            self.delay_spin.setValue(rule.delay_ms)
            self.comment_edit.setText(rule.comment)
    
    def _validate_and_accept(self):
        """Validate inputs before accepting"""
        rule = self.get_rule()
        if rule is not None:
            self._valid_rule = rule
            self.accept()
    
    def get_rule(self) -> Optional[ResponseRule]:
        """Get the rule from dialog inputs"""
        try:
            trigger_text = self.trigger_id_edit.text().strip()
            if not trigger_text:
                raise ValueError("Trigger ID is required")
            trigger_id = int(trigger_text, 16)
            
            response_text = self.response_id_edit.text().strip()
            if not response_text:
                raise ValueError("Response ID is required")
            response_id = int(response_text, 16)
            
            # Parse data bytes
            data_text = self.response_data_edit.text().strip()
            data_bytes = [int(b, 16) for b in data_text.split()] if data_text else []
            
            if len(data_bytes) > 8:
                raise ValueError("Data must be 8 bytes or less")
            
            # Pad to 8 bytes
            while len(data_bytes) < 8:
                data_bytes.append(0)
            
            return ResponseRule(
                trigger_id=trigger_id,
                response_id=response_id,
                response_data=data_bytes,
                is_extended=self.extended_check.isChecked(),
                delay_ms=self.delay_spin.value(),
                comment=self.comment_edit.text().strip()
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return None
    
    def get_validated_rule(self) -> Optional[ResponseRule]:
        """Get the validated rule after dialog closes"""
        return getattr(self, '_valid_rule', None)


class NewTransmitMessageDialog(QDialog):
    """Dialog for creating a new transmit message (like PCAN-View)"""
    
    def __init__(self, parent=None, msg: Optional[TransmitMessage] = None):
        super().__init__(parent)
        self.setWindowTitle("New Transmit Message" if msg is None else "Edit Transmit Message")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        # Top row: ID, Length, Data
        top_layout = QHBoxLayout()
        
        # ID
        id_layout = QVBoxLayout()
        id_layout.addWidget(QLabel("ID: (hex)"))
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("000")
        self.id_edit.setMaximumWidth(100)
        id_layout.addWidget(self.id_edit)
        top_layout.addLayout(id_layout)
        
        # Length
        len_layout = QVBoxLayout()
        len_layout.addWidget(QLabel("Length:"))
        self.length_combo = QComboBox()
        self.length_combo.addItems([str(i) for i in range(9)])
        self.length_combo.setCurrentIndex(8)
        self.length_combo.currentIndexChanged.connect(self._update_data_fields)
        len_layout.addWidget(self.length_combo)
        top_layout.addLayout(len_layout)
        
        # Data bytes with auto-tab
        data_layout = QVBoxLayout()
        data_layout.addWidget(QLabel("Data: (hex)"))
        data_bytes_layout = QHBoxLayout()
        self.data_edits = []
        for i in range(8):
            edit = HexByteLineEdit()
            self.data_edits.append(edit)
            byte_layout = QVBoxLayout()
            byte_layout.addWidget(edit)
            byte_layout.addWidget(QLabel(str(i)))
            data_bytes_layout.addLayout(byte_layout)
        
        # Set up next_edit chain for auto-tab
        for i in range(7):
            self.data_edits[i].set_next_edit(self.data_edits[i + 1])
        
        data_layout.addLayout(data_bytes_layout)
        top_layout.addLayout(data_layout)
        
        layout.addLayout(top_layout)
        
        # Middle row: Cycle Time, Message Type
        middle_layout = QHBoxLayout()
        
        # Cycle Time
        cycle_layout = QVBoxLayout()
        cycle_layout.addWidget(QLabel("Cycle Time:"))
        cycle_inner = QHBoxLayout()
        self.cycle_time_spin = QSpinBox()
        self.cycle_time_spin.setRange(0, 100000)
        self.cycle_time_spin.setValue(100)
        cycle_inner.addWidget(self.cycle_time_spin)
        cycle_inner.addWidget(QLabel("ms"))
        cycle_layout.addLayout(cycle_inner)
        
        self.paused_check = QCheckBox("Paused")
        cycle_layout.addWidget(self.paused_check)
        middle_layout.addLayout(cycle_layout)
        
        # Message Type
        type_group = QGroupBox("Message Type")
        type_layout = QVBoxLayout(type_group)
        self.extended_check = QCheckBox("Extended Frame")
        self.extended_check.setChecked(True)
        type_layout.addWidget(self.extended_check)
        self.remote_check = QCheckBox("Remote Request")
        type_layout.addWidget(self.remote_check)
        middle_layout.addWidget(type_group)
        
        middle_layout.addStretch()
        layout.addLayout(middle_layout)
        
        # Comment
        comment_layout = QHBoxLayout()
        comment_layout.addWidget(QLabel("Comment:"))
        self.comment_edit = QLineEdit()
        comment_layout.addWidget(self.comment_edit)
        layout.addLayout(comment_layout)
        
        # Buttons
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        
        # Fill in existing message data if editing
        if msg:
            self.id_edit.setText(f"{msg.msg_id:X}")
            self.length_combo.setCurrentIndex(len(msg.data))
            for i, b in enumerate(msg.data):
                if i < 8:
                    self.data_edits[i].setText(f"{b:02X}")
            self.cycle_time_spin.setValue(msg.cycle_time_ms)
            self.paused_check.setChecked(msg.is_paused)
            self.extended_check.setChecked(msg.is_extended)
            self.comment_edit.setText(msg.comment)
    
    def _update_data_fields(self, length: int):
        """Enable/disable data fields based on length"""
        for i, edit in enumerate(self.data_edits):
            edit.setEnabled(i < length)
            if i >= length:
                edit.setText("00")
    
    def _validate_and_accept(self):
        """Validate inputs before accepting"""
        msg = self.get_message()
        if msg is not None:
            self._valid_msg = msg
            self.accept()
    
    def get_message(self) -> Optional[TransmitMessage]:
        """Get the transmit message from dialog inputs"""
        try:
            id_text = self.id_edit.text().strip()
            if not id_text:
                raise ValueError("Message ID is required")
            msg_id = int(id_text, 16)
            length = int(self.length_combo.currentText())
            
            # Parse data bytes
            data_bytes = []
            for i in range(length):
                byte_text = self.data_edits[i].text().strip()
                if not byte_text:
                    byte_text = "00"
                data_bytes.append(int(byte_text, 16))
            
            return TransmitMessage(
                msg_id=msg_id,
                data=data_bytes,
                is_extended=self.extended_check.isChecked(),
                cycle_time_ms=self.cycle_time_spin.value(),
                is_paused=self.paused_check.isChecked(),
                comment=self.comment_edit.text().strip()
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", f"Invalid hex value: {e}")
            return None
    
    def get_validated_message(self) -> Optional[TransmitMessage]:
        """Get the validated message after dialog closes"""
        return getattr(self, '_valid_msg', None)


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CANtroller - Intelligent CAN Bus Tool")
        self.setMinimumSize(1100, 750)
        
        # Enable drag & drop
        self.setAcceptDrops(True)
        
        # CAN Manager
        self.can_manager = CANManager()
        self.can_manager.message_received.connect(self._on_message_received)
        self.can_manager.message_sent.connect(self._on_message_sent)
        self.can_manager.connection_changed.connect(self._on_connection_changed)
        self.can_manager.error_occurred.connect(self._on_error)
        self.can_manager.status_updated.connect(self._on_status_updated)
        
        # Message tracking
        self.receive_messages: Dict[int, dict] = {}  # id -> {msg, count, first_time, last_time, timestamp}
        self.transmit_count: Dict[int, int] = {}
        
        # Local counters for status bar
        self.local_rx_count = 0
        self.local_tx_count = 0
        
        # Filter
        self.filter_text = ""
        
        # Display mode: 'hex', 'decimal', or 'decoded'
        self.display_mode = 'hex'
        
        # CAN ID Database (from CSV/MD import)
        self.id_database: Dict[int, str] = {}  # id -> name
        
        # CAN Block Name -> ID mapping (for signal lookup by block name)
        self.name_to_id: Dict[str, int] = {}  # block_name -> id
        
        # Signal database: CAN ID -> list of signal definitions
        # Each signal: {name, bit_start, bit_length, factor, unit}
        self.signal_database: Dict[int, List[dict]] = {}
        
        # Current config file
        self.current_file = None
        
        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()
        
        # Update timer for cycle time calculations and status
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_cycle_times)
        self.update_timer.start(100)
        
        # Status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status_bar)
        self.status_timer.start(250)
        
        # Load settings and auto-open last file
        self._load_settings()
        self._update_window_title()
    
    def _setup_ui(self):
        """Setup the main UI layout"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Main splitter (Receive top, Transmit/Rules bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # === RECEIVE SECTION ===
        receive_group = QGroupBox("Receive")
        receive_layout = QVBoxLayout(receive_group)
        receive_layout.setContentsMargins(5, 10, 5, 5)
        
        # Filter bar
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("🔍 Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Enter CAN ID to filter (hex), e.g., 18F81280")
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.filter_edit)
        
        self.filter_clear_btn = QPushButton("✖")
        self.filter_clear_btn.setMaximumWidth(30)
        self.filter_clear_btn.clicked.connect(self._clear_filter)
        filter_layout.addWidget(self.filter_clear_btn)
        
        receive_layout.addLayout(filter_layout)
        
        self.receive_table = QTableWidget()
        self.receive_table.setColumnCount(8)
        self.receive_table.setHorizontalHeaderLabels([
            "Timestamp", "CAN-ID", "Name", "Type", "Length", "Data", "Cycle Time", "Count"
        ])
        # Interactive mode with last column stretching
        header = self.receive_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)  # Last column stretches
        # Set initial column widths
        self.receive_table.setColumnWidth(0, 90)   # Timestamp
        self.receive_table.setColumnWidth(1, 100)  # CAN-ID
        self.receive_table.setColumnWidth(2, 100)  # Name
        self.receive_table.setColumnWidth(3, 45)   # Type
        self.receive_table.setColumnWidth(4, 50)   # Length
        self.receive_table.setColumnWidth(5, 180)  # Data
        self.receive_table.setColumnWidth(6, 70)   # Cycle Time
        self.receive_table.setAlternatingRowColors(True)
        self.receive_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.receive_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.receive_table.customContextMenuRequested.connect(self._show_receive_context_menu)
        # Click on Data header to toggle HEX/Decimal
        self.receive_table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        receive_layout.addWidget(self.receive_table)
        
        splitter.addWidget(receive_group)
        
        # === TRANSMIT SECTION ===
        transmit_group = QGroupBox("Transmit")
        transmit_layout = QVBoxLayout(transmit_group)
        transmit_layout.setContentsMargins(5, 10, 5, 5)
        
        # Tab widget for Periodic Messages and Response Rules
        self.transmit_tabs = QTabWidget()
        
        # --- Tab 1: Periodic Messages ---
        periodic_widget = QWidget()
        periodic_layout = QVBoxLayout(periodic_widget)
        periodic_layout.setContentsMargins(0, 5, 0, 0)
        
        # Toolbar for periodic messages
        periodic_toolbar = QHBoxLayout()
        
        self.new_msg_btn = QPushButton("📧 New Message...")
        self.new_msg_btn.clicked.connect(self._new_transmit_message)
        periodic_toolbar.addWidget(self.new_msg_btn)
        
        self.edit_msg_btn = QPushButton("✏️ Edit...")
        self.edit_msg_btn.clicked.connect(self._edit_transmit_message)
        periodic_toolbar.addWidget(self.edit_msg_btn)
        
        self.delete_msg_btn = QPushButton("🗑️ Delete")
        self.delete_msg_btn.clicked.connect(self._delete_transmit_message)
        periodic_toolbar.addWidget(self.delete_msg_btn)
        
        self.send_once_btn = QPushButton("📤 Send Once")
        self.send_once_btn.clicked.connect(self._send_message_once)
        periodic_toolbar.addWidget(self.send_once_btn)
        
        self.toggle_msg_btn = QPushButton("⏯️ Toggle Pause")
        self.toggle_msg_btn.clicked.connect(self._toggle_message_pause)
        periodic_toolbar.addWidget(self.toggle_msg_btn)
        
        periodic_toolbar.addStretch()
        periodic_layout.addLayout(periodic_toolbar)
        
        self.periodic_table = QTableWidget()
        self.periodic_table.setColumnCount(7)
        self.periodic_table.setHorizontalHeaderLabels([
            "CAN-ID", "Type", "Length", "Data", "Cycle Time", "Count", "Comment"
        ])
        # Interactive mode with last column stretching
        header = self.periodic_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)  # Comment stretches
        self.periodic_table.setColumnWidth(0, 100)  # CAN-ID
        self.periodic_table.setColumnWidth(1, 45)   # Type
        self.periodic_table.setColumnWidth(2, 50)   # Length
        self.periodic_table.setColumnWidth(3, 180)  # Data
        self.periodic_table.setColumnWidth(4, 80)   # Cycle Time
        self.periodic_table.setColumnWidth(5, 55)   # Count
        self.periodic_table.setAlternatingRowColors(True)
        self.periodic_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.periodic_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.periodic_table.customContextMenuRequested.connect(self._show_periodic_context_menu)
        self.periodic_table.doubleClicked.connect(self._edit_transmit_message)
        periodic_layout.addWidget(self.periodic_table)
        
        self.transmit_tabs.addTab(periodic_widget, "📧 Periodic Messages")
        
        # --- Tab 2: Response Rules ---
        rules_widget = QWidget()
        rules_layout = QVBoxLayout(rules_widget)
        rules_layout.setContentsMargins(0, 5, 0, 0)
        
        # Toolbar for rules
        rules_toolbar = QHBoxLayout()
        
        self.add_rule_btn = QPushButton("➕ Add Rule")
        self.add_rule_btn.clicked.connect(self._add_rule)
        rules_toolbar.addWidget(self.add_rule_btn)
        
        self.edit_rule_btn = QPushButton("✏️ Edit")
        self.edit_rule_btn.clicked.connect(self._edit_rule)
        rules_toolbar.addWidget(self.edit_rule_btn)
        
        self.remove_rule_btn = QPushButton("➖ Remove")
        self.remove_rule_btn.clicked.connect(self._remove_rule)
        rules_toolbar.addWidget(self.remove_rule_btn)
        
        self.response_mode_btn = QPushButton("🔴 Response Mode OFF")
        self.response_mode_btn.setCheckable(True)
        self.response_mode_btn.clicked.connect(self._toggle_response_mode)
        self.response_mode_btn.setStyleSheet("""
            QPushButton { padding: 5px 15px; font-weight: bold; }
            QPushButton:checked { background-color: #4CAF50; color: white; }
        """)
        rules_toolbar.addWidget(self.response_mode_btn)
        
        rules_toolbar.addStretch()
        rules_layout.addLayout(rules_toolbar)
        
        self.rules_table = QTableWidget()
        self.rules_table.setColumnCount(8)
        self.rules_table.setHorizontalHeaderLabels([
            "Trigger ID", "Response ID", "Length", "Data", "Delay", "Count", "Enabled", "Comment"
        ])
        # Interactive mode with last column stretching
        header = self.rules_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)  # Comment stretches
        self.rules_table.setColumnWidth(0, 100)  # Trigger ID
        self.rules_table.setColumnWidth(1, 100)  # Response ID
        self.rules_table.setColumnWidth(2, 50)   # Length
        self.rules_table.setColumnWidth(3, 180)  # Data
        self.rules_table.setColumnWidth(4, 55)   # Delay
        self.rules_table.setColumnWidth(5, 55)   # Count
        self.rules_table.setColumnWidth(6, 55)   # Enabled
        self.rules_table.setAlternatingRowColors(True)
        self.rules_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.rules_table.doubleClicked.connect(self._edit_rule)
        rules_layout.addWidget(self.rules_table)
        
        self.transmit_tabs.addTab(rules_widget, "🔄 Response Rules")
        
        transmit_layout.addWidget(self.transmit_tabs)
        splitter.addWidget(transmit_group)
        
        # Set initial sizes
        splitter.setSizes([400, 300])
        
        layout.addWidget(splitter)
    
    def _setup_menu(self):
        """Setup menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        new_action = QAction("&New", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._new_config)
        file_menu.addAction(new_action)
        
        open_action = QAction("&Open...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_config)
        file_menu.addAction(open_action)
        
        save_action = QAction("&Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_config)
        file_menu.addAction(save_action)
        
        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_config_as)
        file_menu.addAction(save_as_action)
        
        file_menu.addSeparator()
        
        # Export submenu
        export_menu = file_menu.addMenu("&Export")
        
        export_csv_action = QAction("Export Logs as &CSV...", self)
        export_csv_action.triggered.connect(lambda: self._export_logs('csv'))
        export_menu.addAction(export_csv_action)
        
        export_txt_action = QAction("Export Logs as &TXT...", self)
        export_txt_action.triggered.connect(lambda: self._export_logs('txt'))
        export_menu.addAction(export_txt_action)
        
        export_asc_action = QAction("Export Logs as &ASC...", self)
        export_asc_action.triggered.connect(lambda: self._export_logs('asc'))
        export_menu.addAction(export_asc_action)
        
        # Import submenu
        import_menu = file_menu.addMenu("&Import")
        
        import_blocks_action = QAction("Import CAN &Blocks (CSV/MD)...", self)
        import_blocks_action.triggered.connect(self._import_id_database)
        import_menu.addAction(import_blocks_action)
        
        import_signals_action = QAction("Import &Signal Definitions (CSV/MD)...", self)
        import_signals_action.triggered.connect(self._import_signal_database)
        import_menu.addAction(import_signals_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # CAN menu
        can_menu = menubar.addMenu("&CAN")
        
        connect_action = QAction("&Connect", self)
        connect_action.triggered.connect(self._connect)
        can_menu.addAction(connect_action)
        
        disconnect_action = QAction("&Disconnect", self)
        disconnect_action.triggered.connect(self._disconnect)
        can_menu.addAction(disconnect_action)
        
        # Transmit menu
        transmit_menu = menubar.addMenu("&Transmit")
        
        new_msg_action = QAction("&New Message...", self)
        new_msg_action.setShortcut("Ins")
        new_msg_action.triggered.connect(self._new_transmit_message)
        transmit_menu.addAction(new_msg_action)
        
        # View menu
        view_menu = menubar.addMenu("&View")
        
        clear_action = QAction("&Clear Messages", self)
        clear_action.triggered.connect(self._clear_messages)
        view_menu.addAction(clear_action)
    
    def _setup_toolbar(self):
        """Setup toolbar"""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        
        # Channel selector
        toolbar.addWidget(QLabel(" Channel: "))
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(CANManager.CHANNELS)
        toolbar.addWidget(self.channel_combo)
        
        toolbar.addSeparator()
        
        # Bitrate selector
        toolbar.addWidget(QLabel(" Bitrate: "))
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(CANManager.BITRATES.keys())
        toolbar.addWidget(self.bitrate_combo)
        
        toolbar.addSeparator()
        
        # Connect button
        self.connect_btn = QPushButton("🔌 Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.connect_btn.setStyleSheet("padding: 5px 15px;")
        toolbar.addWidget(self.connect_btn)
        
        toolbar.addSeparator()
        
        # Pause button
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.clicked.connect(self._toggle_pause)
        toolbar.addWidget(self.pause_btn)
        
        # Clear button
        self.clear_btn = QPushButton("🗑 Clear")
        self.clear_btn.clicked.connect(self._clear_messages)
        toolbar.addWidget(self.clear_btn)
    
    def _setup_statusbar(self):
        """Setup status bar with detailed CAN bus info"""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        
        # Connection status
        self.status_label = QLabel("Disconnected")
        self.statusbar.addWidget(self.status_label, 1)
        
        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        self.statusbar.addPermanentWidget(sep1)
        
        # Bitrate
        self.bitrate_label = QLabel("Bit rate: ---")
        self.statusbar.addPermanentWidget(self.bitrate_label)
        
        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        self.statusbar.addPermanentWidget(sep2)
        
        # Status
        self.bus_status_label = QLabel("Status: ---")
        self.statusbar.addPermanentWidget(self.bus_status_label)
        
        # Separator
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        self.statusbar.addPermanentWidget(sep3)
        
        # RX Count
        self.rx_label = QLabel("RX: 0")
        self.statusbar.addPermanentWidget(self.rx_label)
        
        # TX Count
        self.tx_label = QLabel("TX: 0")
        self.statusbar.addPermanentWidget(self.tx_label)
        
        # Separator
        sep4 = QFrame()
        sep4.setFrameShape(QFrame.Shape.VLine)
        self.statusbar.addPermanentWidget(sep4)
        
        # Errors
        self.errors_label = QLabel("Errors: 0")
        self.statusbar.addPermanentWidget(self.errors_label)
        
        # Status indicator
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("color: red; font-size: 16px;")
        self.statusbar.addPermanentWidget(self.status_indicator)
    
    def _update_status_bar(self):
        """Update status bar with current counts"""
        self.rx_label.setText(f"RX: {self.local_rx_count}")
        self.tx_label.setText(f"TX: {self.local_tx_count}")
    
    def _toggle_connection(self):
        """Toggle CAN connection"""
        if self.can_manager.is_connected:
            self._disconnect()
        else:
            self._connect()
    
    def _connect(self):
        """Connect to CAN bus"""
        channel = self.channel_combo.currentText()
        bitrate_text = self.bitrate_combo.currentText()
        bitrate = CANManager.BITRATES[bitrate_text]
        
        if self.can_manager.connect(channel, bitrate):
            self.connect_btn.setText("🔌 Disconnect")
            self.channel_combo.setEnabled(False)
            self.bitrate_combo.setEnabled(False)
            # Reset local counters
            self.local_rx_count = 0
            self.local_tx_count = 0
            # Start any non-paused transmissions
            self.can_manager.start_all_transmissions()
    
    def _disconnect(self):
        """Disconnect from CAN bus"""
        self.can_manager.disconnect()
        self.connect_btn.setText("🔌 Connect")
        self.channel_combo.setEnabled(True)
        self.bitrate_combo.setEnabled(True)
    
    def _toggle_pause(self):
        """Toggle message reception pause"""
        self.can_manager.paused = self.pause_btn.isChecked()
        if self.pause_btn.isChecked():
            self.pause_btn.setText("▶ Resume")
        else:
            self.pause_btn.setText("⏸ Pause")
    
    def _clear_messages(self):
        """Clear all received messages"""
        self.receive_messages.clear()
        self.receive_table.setRowCount(0)
    
    def _on_filter_changed(self, text: str):
        """Handle filter text change"""
        self.filter_text = text.strip().upper()
        self._update_receive_table()
    
    def _clear_filter(self):
        """Clear the filter"""
        self.filter_edit.clear()
        self.filter_text = ""
        self._update_receive_table()
    
    def _show_receive_context_menu(self, pos):
        """Show context menu for receive table"""
        menu = QMenu(self)
        
        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_receive_selection)
        menu.addAction(copy_action)
        
        menu.addSeparator()
        
        clear_action = QAction("Clear All", self)
        clear_action.triggered.connect(self._clear_messages)
        menu.addAction(clear_action)
        
        menu.exec(self.receive_table.mapToGlobal(pos))
    
    def _copy_receive_selection(self):
        """Copy selected receive message to clipboard"""
        row = self.receive_table.currentRow()
        if row >= 0:
            data = []
            for col in range(self.receive_table.columnCount()):
                item = self.receive_table.item(row, col)
                if item:
                    data.append(item.text())
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText("\t".join(data))
    
    def _show_periodic_context_menu(self, pos):
        """Show context menu for periodic messages table"""
        menu = QMenu(self)
        
        new_action = QAction("New Message...", self)
        new_action.triggered.connect(self._new_transmit_message)
        menu.addAction(new_action)
        
        edit_action = QAction("Edit Message...", self)
        edit_action.triggered.connect(self._edit_transmit_message)
        menu.addAction(edit_action)
        
        menu.addSeparator()
        
        send_action = QAction("Send Once", self)
        send_action.triggered.connect(self._send_message_once)
        menu.addAction(send_action)
        
        toggle_action = QAction("Toggle Pause", self)
        toggle_action.triggered.connect(self._toggle_message_pause)
        menu.addAction(toggle_action)
        
        menu.addSeparator()
        
        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(self._delete_transmit_message)
        menu.addAction(delete_action)
        
        clear_action = QAction("Clear All", self)
        clear_action.triggered.connect(self._clear_all_periodic)
        menu.addAction(clear_action)
        
        menu.exec(self.periodic_table.mapToGlobal(pos))
    
    def _on_message_received(self, msg: can.Message):
        """Handle received CAN message"""
        msg_id = msg.arbitration_id
        current_time = time.time()
        
        # Increment local RX counter
        self.local_rx_count += 1
        
        if msg_id in self.receive_messages:
            entry = self.receive_messages[msg_id]
            entry['count'] += 1
            entry['last_time'] = current_time
            entry['msg'] = msg
        else:
            self.receive_messages[msg_id] = {
                'msg': msg,
                'count': 1,
                'first_time': current_time,
                'last_time': current_time
            }
        
        self._update_receive_table()
    
    def _on_message_sent(self, msg: can.Message):
        """Handle sent CAN message"""
        msg_id = msg.arbitration_id
        
        # Increment local TX counter
        self.local_tx_count += 1
        
        if msg_id in self.transmit_count:
            self.transmit_count[msg_id] += 1
        else:
            self.transmit_count[msg_id] = 1
        self._update_periodic_table()
        self._update_rules_table()
    
    def _update_receive_table(self):
        """Update the receive messages table"""
        # Filter messages
        filtered_messages = {}
        for msg_id, entry in self.receive_messages.items():
            if self.filter_text:
                id_hex = f"{msg_id:08X}" if entry['msg'].is_extended_id else f"{msg_id:03X}"
                # Also search by name
                name = self.id_database.get(msg_id, '')
                if self.filter_text not in id_hex and self.filter_text.lower() not in name.lower():
                    continue
            filtered_messages[msg_id] = entry
        
        self.receive_table.setRowCount(len(filtered_messages))
        
        for row, (msg_id, entry) in enumerate(sorted(filtered_messages.items())):
            msg = entry['msg']
            
            # Column 0: Timestamp
            timestamp = datetime.fromtimestamp(entry['last_time']).strftime('%H:%M:%S.') + \
                       f"{int((entry['last_time'] % 1) * 1000):03d}"
            ts_item = QTableWidgetItem(timestamp)
            ts_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.receive_table.setItem(row, 0, ts_item)
            
            # Column 1: CAN-ID
            id_item = QTableWidgetItem(f"{msg_id:08X}h" if msg.is_extended_id else f"{msg_id:03X}h")
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.receive_table.setItem(row, 1, id_item)
            
            # Column 2: Name (from database)
            name = self.id_database.get(msg_id, '')
            name_item = QTableWidgetItem(name)
            self.receive_table.setItem(row, 2, name_item)
            
            # Column 3: Type
            type_text = "Ext" if msg.is_extended_id else "Std"
            type_item = QTableWidgetItem(type_text)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.receive_table.setItem(row, 3, type_item)
            
            # Column 4: Length
            length_item = QTableWidgetItem(str(len(msg.data)))
            length_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.receive_table.setItem(row, 4, length_item)
            
            # Column 5: Data (HEX, Decimal, or Decoded based on display_mode)
            if self.display_mode == 'decoded' and msg_id in self.signal_database:
                data_str = self._decode_signals(msg_id, msg.data)
            elif self.display_mode == 'decimal':
                data_str = " ".join(str(b) for b in msg.data)
            else:
                data_str = " ".join(f"{b:02X}" for b in msg.data)
            data_item = QTableWidgetItem(data_str)
            self.receive_table.setItem(row, 5, data_item)
            
            # Column 6: Cycle Time (calculated from count and time span)
            if entry['count'] > 1:
                time_span = entry['last_time'] - entry['first_time']
                if time_span > 0:
                    cycle_time = (time_span / (entry['count'] - 1)) * 1000  # ms
                    cycle_item = QTableWidgetItem(f"{cycle_time:.1f}")
                else:
                    cycle_item = QTableWidgetItem("-")
            else:
                cycle_item = QTableWidgetItem("-")
            cycle_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.receive_table.setItem(row, 6, cycle_item)
            
            # Column 7: Count
            count_item = QTableWidgetItem(str(entry['count']))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.receive_table.setItem(row, 7, count_item)
    
    def _update_periodic_table(self):
        """Update the periodic messages table"""
        messages = self.can_manager.get_transmit_messages()
        self.periodic_table.setRowCount(len(messages))
        
        for row, msg in enumerate(messages):
            # CAN-ID
            id_item = QTableWidgetItem(f"{msg.msg_id:08X}h" if msg.is_extended else f"{msg.msg_id:03X}h")
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # Color based on pause state
            if msg.is_paused:
                id_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 0, id_item)
            
            # Type
            type_text = "Ext" if msg.is_extended else "Std"
            type_item = QTableWidgetItem(type_text)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if msg.is_paused:
                type_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 1, type_item)
            
            # Length
            length_item = QTableWidgetItem(str(len(msg.data)))
            length_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if msg.is_paused:
                length_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 2, length_item)
            
            # Data
            data_str = " ".join(f"{b:02X}" for b in msg.data)
            data_item = QTableWidgetItem(data_str)
            if msg.is_paused:
                data_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 3, data_item)
            
            # Cycle Time
            cycle_text = f"{msg.cycle_time_ms} ms" if msg.cycle_time_ms > 0 else "Manual"
            if msg.is_paused:
                cycle_text = f"⏸ {cycle_text}"
            cycle_item = QTableWidgetItem(cycle_text)
            cycle_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if msg.is_paused:
                cycle_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 4, cycle_item)
            
            # Count
            count_item = QTableWidgetItem(str(msg.count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if msg.is_paused:
                count_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 5, count_item)
            
            # Comment
            comment_item = QTableWidgetItem(msg.comment)
            if msg.is_paused:
                comment_item.setBackground(QColor("#555"))
            self.periodic_table.setItem(row, 6, comment_item)
    
    def _update_rules_table(self):
        """Update the response rules table"""
        rules = self.can_manager.get_response_rules()
        self.rules_table.setRowCount(len(rules))
        
        for row, rule in enumerate(rules):
            # Trigger ID
            trigger_item = QTableWidgetItem(f"{rule.trigger_id:08X}h")
            trigger_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rules_table.setItem(row, 0, trigger_item)
            
            # Response ID
            response_item = QTableWidgetItem(f"{rule.response_id:08X}h")
            response_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rules_table.setItem(row, 1, response_item)
            
            # Length
            length_item = QTableWidgetItem(str(len(rule.response_data)))
            length_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rules_table.setItem(row, 2, length_item)
            
            # Data
            data_str = " ".join(f"{b:02X}" for b in rule.response_data)
            data_item = QTableWidgetItem(data_str)
            self.rules_table.setItem(row, 3, data_item)
            
            # Delay
            delay_item = QTableWidgetItem(f"{rule.delay_ms} ms")
            delay_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rules_table.setItem(row, 4, delay_item)
            
            # Count
            count = self.transmit_count.get(rule.response_id, 0)
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rules_table.setItem(row, 5, count_item)
            
            # Enabled
            enabled_item = QTableWidgetItem("✓" if rule.enabled else "✗")
            enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rules_table.setItem(row, 6, enabled_item)
            
            # Comment
            comment_item = QTableWidgetItem(rule.comment)
            self.rules_table.setItem(row, 7, comment_item)
    
    def _update_cycle_times(self):
        """Periodic update for cycle times display"""
        if self.can_manager.is_connected and not self.can_manager.paused:
            self._update_receive_table()
            self._update_periodic_table()
    
    # === Periodic Messages ===
    
    def _new_transmit_message(self):
        """Create a new periodic transmit message"""
        dialog = NewTransmitMessageDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            msg = dialog.get_validated_message()
            if msg:
                self.can_manager.add_transmit_message(msg)
                self._update_periodic_table()
    
    def _edit_transmit_message(self):
        """Edit selected periodic message"""
        row = self.periodic_table.currentRow()
        if row >= 0:
            messages = self.can_manager.get_transmit_messages()
            if row < len(messages):
                dialog = NewTransmitMessageDialog(self, messages[row])
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    new_msg = dialog.get_validated_message()
                    if new_msg:
                        # Remove old and add new
                        self.can_manager.remove_transmit_message(row)
                        self.can_manager.add_transmit_message(new_msg)
                        self._update_periodic_table()
    
    def _delete_transmit_message(self):
        """Delete selected periodic message"""
        row = self.periodic_table.currentRow()
        if row >= 0:
            self.can_manager.remove_transmit_message(row)
            self._update_periodic_table()
    
    def _send_message_once(self):
        """Send selected message once"""
        row = self.periodic_table.currentRow()
        if row >= 0:
            self.can_manager.send_transmit_message_once(row)
            self._update_periodic_table()
    
    def _toggle_message_pause(self):
        """Toggle pause state of selected message"""
        row = self.periodic_table.currentRow()
        if row >= 0:
            self.can_manager.toggle_transmit_message(row)
            self._update_periodic_table()
    
    def _clear_all_periodic(self):
        """Clear all periodic messages"""
        self.can_manager.clear_transmit_messages()
        self._update_periodic_table()
    
    # === Response Rules ===
    
    def _add_rule(self):
        """Add a new response rule"""
        dialog = AddRuleDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule = dialog.get_validated_rule()
            if rule:
                self.can_manager.add_response_rule(rule)
                self._update_rules_table()
    
    def _remove_rule(self):
        """Remove selected response rule"""
        row = self.rules_table.currentRow()
        if row >= 0:
            self.can_manager.remove_response_rule(row)
            self._update_rules_table()
    
    def _toggle_response_mode(self):
        """Toggle automatic response mode"""
        enabled = self.response_mode_btn.isChecked()
        self.can_manager.response_mode_enabled = enabled
        
        if enabled:
            self.response_mode_btn.setText("🟢 Response Mode ON")
        else:
            self.response_mode_btn.setText("🔴 Response Mode OFF")
    
    def _on_connection_changed(self, connected: bool, status: str):
        """Handle connection status changes"""
        self.status_label.setText(status)
        
        if connected:
            self.status_indicator.setStyleSheet("color: #4CAF50; font-size: 16px;")
            self.bitrate_label.setText(f"Bit rate: {self.bitrate_combo.currentText()}")
            self.bus_status_label.setText("Status: OK")
        else:
            self.status_indicator.setStyleSheet("color: red; font-size: 16px;")
            self.bitrate_label.setText("Bit rate: ---")
            self.bus_status_label.setText("Status: ---")
            self.rx_label.setText("RX: 0")
            self.tx_label.setText("TX: 0")
            self.errors_label.setText("Errors: 0")
    
    def _on_status_updated(self, status: dict):
        """Handle status updates from CAN manager"""
        errors = status.get('errors', 0)
        self.errors_label.setText(f"Errors: {errors}")
        if errors > 0:
            self.errors_label.setStyleSheet("color: #ff6b6b;")
        else:
            self.errors_label.setStyleSheet("")
    
    def _on_error(self, error_msg: str):
        """Handle error messages"""
        QMessageBox.warning(self, "Error", error_msg)
    
    def closeEvent(self, event):
        """Handle window close"""
        if self.can_manager.is_connected:
            self.can_manager.disconnect()
        event.accept()
    
    # === Save/Load Configuration ===
    
    def _update_window_title(self):
        """Update window title with current file name"""
        base_title = "CANtroller - Intelligent CAN Bus Tool"
        if self.current_file:
            filename = os.path.basename(self.current_file)
            self.setWindowTitle(f"{base_title} - {filename}")
        else:
            self.setWindowTitle(base_title)
    
    def _new_config(self):
        """Create new configuration (clear all)"""
        reply = QMessageBox.question(
            self, "New Configuration",
            "This will clear all messages and rules. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.can_manager.clear_transmit_messages()
            self.can_manager.clear_response_rules()
            self.current_file = None
            self._update_window_title()
            self._update_periodic_table()
            self._update_rules_table()
    
    def _open_config(self):
        """Open configuration file"""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open Configuration",
            "",
            "CANtroller Config (*.cantroller);;JSON Files (*.json);;All Files (*)"
        )
        if filename:
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # Clear existing
                self.can_manager.clear_transmit_messages()
                self.can_manager.clear_response_rules()
                
                # Load periodic messages
                for msg_data in config.get('periodic_messages', []):
                    msg = TransmitMessage(
                        msg_id=msg_data['msg_id'],
                        data=msg_data['data'],
                        is_extended=msg_data.get('is_extended', True),
                        cycle_time_ms=msg_data.get('cycle_time_ms', 100),
                        is_paused=msg_data.get('is_paused', False),
                        comment=msg_data.get('comment', '')
                    )
                    self.can_manager.add_transmit_message(msg)
                
                # Load response rules
                for rule_data in config.get('response_rules', []):
                    rule = ResponseRule(
                        trigger_id=rule_data['trigger_id'],
                        response_id=rule_data['response_id'],
                        response_data=rule_data['response_data'],
                        is_extended=rule_data.get('is_extended', True),
                        delay_ms=rule_data.get('delay_ms', 0),
                        comment=rule_data.get('comment', ''),
                        enabled=rule_data.get('enabled', True)
                    )
                    self.can_manager.add_response_rule(rule)
                
                # Load connection settings if present
                if 'settings' in config:
                    settings = config['settings']
                    channel = settings.get('channel', 'PCAN_USBBUS1')
                    bitrate = settings.get('bitrate', '500 kbit/s')
                    
                    # Set combo boxes
                    idx = self.channel_combo.findText(channel)
                    if idx >= 0:
                        self.channel_combo.setCurrentIndex(idx)
                    idx = self.bitrate_combo.findText(bitrate)
                    if idx >= 0:
                        self.bitrate_combo.setCurrentIndex(idx)
                
                self.current_file = filename
                self._update_window_title()
                self._update_periodic_table()
                self._update_rules_table()
                
                QMessageBox.information(self, "Success", f"Configuration loaded from:\n{filename}")
                
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load configuration:\n{str(e)}")
    
    def _save_config(self):
        """Save configuration to current file or prompt for new file"""
        if self.current_file:
            self._save_to_file(self.current_file)
        else:
            self._save_config_as()
    
    def _save_config_as(self):
        """Save configuration to a new file"""
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Configuration",
            "",
            "CANtroller Config (*.cantroller);;JSON Files (*.json);;All Files (*)"
        )
        if filename:
            # Ensure extension
            if not filename.endswith('.cantroller') and not filename.endswith('.json'):
                filename += '.cantroller'
            self._save_to_file(filename)
    
    def _save_to_file(self, filename: str):
        """Save configuration to specified file"""
        try:
            config = {
                'version': '1.0',
                'settings': {
                    'channel': self.channel_combo.currentText(),
                    'bitrate': self.bitrate_combo.currentText()
                },
                'periodic_messages': [],
                'response_rules': []
            }
            
            # Save periodic messages
            for msg in self.can_manager.get_transmit_messages():
                config['periodic_messages'].append({
                    'msg_id': msg.msg_id,
                    'data': msg.data,
                    'is_extended': msg.is_extended,
                    'cycle_time_ms': msg.cycle_time_ms,
                    'is_paused': msg.is_paused,
                    'comment': msg.comment
                })
            
            # Save response rules
            for rule in self.can_manager.get_response_rules():
                config['response_rules'].append({
                    'trigger_id': rule.trigger_id,
                    'response_id': rule.response_id,
                    'response_data': rule.response_data,
                    'is_extended': rule.is_extended,
                    'delay_ms': rule.delay_ms,
                    'comment': rule.comment,
                    'enabled': rule.enabled
                })
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            
            self.current_file = filename
            self._update_window_title()
            self._save_settings()  # Save last file path
            
            QMessageBox.information(self, "Success", f"Configuration saved to:\n{filename}")
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save configuration:\n{str(e)}")
    
    # === Settings Persistence ===
    
    def _load_settings(self):
        """Load application settings from file"""
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                
                # Restore display mode
                self.display_mode = settings.get('display_mode', 'hex')
                
                # Restore ID database
                id_db = settings.get('id_database', {})
                self.id_database = {int(k): v for k, v in id_db.items()}
                
                # Restore signal database
                sig_db = settings.get('signal_database', {})
                self.signal_database = {int(k): v for k, v in sig_db.items()}
                
                # Restore name_to_id mapping
                self.name_to_id = settings.get('name_to_id', {})
                
                # Auto-open last file
                last_file = settings.get('last_file')
                if last_file and os.path.exists(last_file):
                    self._load_config_file(last_file)
        except Exception:
            pass  # Ignore errors, use defaults
    
    def _save_settings(self):
        """Save application settings to file"""
        try:
            settings = {
                'last_file': self.current_file,
                'display_mode': self.display_mode,
                'id_database': {str(k): v for k, v in self.id_database.items()},
                'signal_database': {str(k): v for k, v in self.signal_database.items()},
                'name_to_id': self.name_to_id
            }
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass  # Ignore errors
    
    def _load_config_file(self, filename: str):
        """Load configuration from file (internal helper)"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Clear existing
            self.can_manager.clear_transmit_messages()
            self.can_manager.clear_response_rules()
            
            # Load periodic messages
            for msg_data in config.get('periodic_messages', []):
                msg = TransmitMessage(
                    msg_id=msg_data['msg_id'],
                    data=msg_data['data'],
                    is_extended=msg_data.get('is_extended', True),
                    cycle_time_ms=msg_data.get('cycle_time_ms', 100),
                    is_paused=msg_data.get('is_paused', False),
                    comment=msg_data.get('comment', '')
                )
                self.can_manager.add_transmit_message(msg)
            
            # Load response rules
            for rule_data in config.get('response_rules', []):
                rule = ResponseRule(
                    trigger_id=rule_data['trigger_id'],
                    response_id=rule_data['response_id'],
                    response_data=rule_data['response_data'],
                    is_extended=rule_data.get('is_extended', True),
                    delay_ms=rule_data.get('delay_ms', 0),
                    comment=rule_data.get('comment', ''),
                    enabled=rule_data.get('enabled', True)
                )
                self.can_manager.add_response_rule(rule)
            
            # Load connection settings if present
            if 'settings' in config:
                settings = config['settings']
                channel = settings.get('channel', 'PCAN_USBBUS1')
                bitrate = settings.get('bitrate', '500 kbit/s')
                
                idx = self.channel_combo.findText(channel)
                if idx >= 0:
                    self.channel_combo.setCurrentIndex(idx)
                idx = self.bitrate_combo.findText(bitrate)
                if idx >= 0:
                    self.bitrate_combo.setCurrentIndex(idx)
            
            self.current_file = filename
            self._update_window_title()
            self._update_periodic_table()
            self._update_rules_table()
            self._save_settings()
            return True
        except Exception:
            return False
    
    # === Drag & Drop ===
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """Handle drag enter for .cantroller files"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().endswith('.cantroller') or url.toLocalFile().endswith('.json'):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        """Handle drop for .cantroller files"""
        for url in event.mimeData().urls():
            filepath = url.toLocalFile()
            if filepath.endswith('.cantroller') or filepath.endswith('.json'):
                if self._load_config_file(filepath):
                    QMessageBox.information(self, "Success", f"Configuration loaded from:\n{filepath}")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to load configuration:\n{filepath}")
                break
    
    # === Export Logs ===
    
    def _export_logs(self, format_type: str):
        """Export received messages to file"""
        if not self.receive_messages:
            QMessageBox.information(self, "Export", "No messages to export.")
            return
        
        extensions = {'csv': 'CSV Files (*.csv)', 'txt': 'Text Files (*.txt)', 'asc': 'ASC Files (*.asc)'}
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs",
            f"can_log.{format_type}",
            extensions.get(format_type, 'All Files (*)')
        )
        
        if not filename:
            return
        
        try:
            with open(filename, 'w', encoding='utf-8', newline='') as f:
                if format_type == 'csv':
                    writer = csv.writer(f)
                    writer.writerow(['Timestamp', 'CAN-ID', 'Name', 'Type', 'Length', 'Data', 'Count'])
                    for msg_id, entry in sorted(self.receive_messages.items()):
                        msg = entry['msg']
                        id_hex = f"{msg_id:08X}" if msg.is_extended_id else f"{msg_id:03X}"
                        name = self.id_database.get(msg_id, '')
                        data_str = ' '.join(f"{b:02X}" for b in msg.data)
                        timestamp = datetime.fromtimestamp(entry['last_time']).strftime('%H:%M:%S.%f')[:-3]
                        writer.writerow([timestamp, id_hex, name, 'Ext' if msg.is_extended_id else 'Std', 
                                        len(msg.data), data_str, entry['count']])
                
                elif format_type == 'txt':
                    for msg_id, entry in sorted(self.receive_messages.items()):
                        msg = entry['msg']
                        id_hex = f"{msg_id:08X}" if msg.is_extended_id else f"{msg_id:03X}"
                        data_str = ' '.join(f"{b:02X}" for b in msg.data)
                        timestamp = datetime.fromtimestamp(entry['last_time']).strftime('%H:%M:%S.%f')[:-3]
                        f.write(f"{timestamp}  {id_hex}  [{len(msg.data)}]  {data_str}\n")
                
                elif format_type == 'asc':
                    f.write("date " + datetime.now().strftime("%a %b %d %I:%M:%S %p %Y") + "\n")
                    f.write("base hex  timestamps absolute\n")
                    f.write("Begin Triggerblock\n")
                    for msg_id, entry in sorted(self.receive_messages.items()):
                        msg = entry['msg']
                        data_str = ' '.join(f"{b:02X}" for b in msg.data)
                        timestamp = entry['last_time']
                        f.write(f"   {timestamp:.6f} 1  {msg_id:08X}x       Rx   d {len(msg.data)}  {data_str}\n")
                    f.write("End Triggerblock\n")
            
            QMessageBox.information(self, "Export", f"Logs exported to:\n{filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to export logs:\n{str(e)}")
    
    # === Import CSV Database ===
    
    def _import_id_database(self):
        """Import CAN ID names from CSV or MD file"""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import CAN ID Database",
            "",
            "All Supported (*.csv *.md);;CSV Files (*.csv);;Markdown Files (*.md);;All Files (*)"
        )
        
        if not filename:
            return
        
        try:
            count = 0
            if filename.endswith('.md'):
                count = self._import_md_blocks(filename)
            else:
                count = self._import_csv_blocks(filename)
            
            self._save_settings()
            self._update_receive_table()
            QMessageBox.information(self, "Import", f"Imported {count} CAN ID entries from:\n{filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to import database:\n{str(e)}")
    
    def _import_csv_blocks(self, filename: str) -> int:
        """Import CAN IDs from CSV file (CAN bus Nr,Name,CAN ID [hex],...)"""
        count = 0
        with open(filename, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            header = next(reader, None)  # Skip header
            
            for row in reader:
                if len(row) < 3:
                    continue
                try:
                    # Format: CAN bus Nr, Name, CAN ID [hex], ...
                    name = row[1].strip()
                    can_id_str = row[2].strip().replace('0x', '').replace('h', '')
                    msg_id = int(can_id_str, 16)
                    
                    if msg_id and name:
                        self.id_database[msg_id] = name
                        self.name_to_id[name.upper()] = msg_id
                        count += 1
                except (ValueError, IndexError):
                    continue
        return count
    
    def _import_md_blocks(self, filename: str) -> int:
        """Import CAN IDs from Notion-exported Markdown table"""
        count = 0
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            if not line.startswith('|') or '---' in line:
                continue
            
            parts = [p.strip() for p in line.split('|')[1:-1]]
            if len(parts) < 3 or 'CAN ID' in parts[2] or 'Name' in parts[1]:
                continue
            
            try:
                name = parts[1].strip().replace('**', '')
                can_id_str = parts[2].strip().replace('0x', '').replace('**', '')
                msg_id = int(can_id_str, 16)
                
                if msg_id and name:
                    self.id_database[msg_id] = name
                    self.name_to_id[name.upper()] = msg_id
                    count += 1
            except (ValueError, IndexError):
                continue
        return count
    
    def _import_signal_database(self):
        """Import signal definitions from CSV or MD file"""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import Signal Definitions",
            "",
            "All Supported (*.csv *.md);;Markdown Files (*.md);;CSV Files (*.csv);;All Files (*)"
        )
        
        if not filename:
            return
        
        try:
            count = self._import_md_signals(filename) if filename.endswith('.md') else self._import_csv_signals(filename)
            self._save_settings()
            # Show which CAN IDs have signals defined
            can_ids = [f"0x{cid:08X}" for cid in self.signal_database.keys()]
            ids_str = ", ".join(can_ids[:5]) + ("..." if len(can_ids) > 5 else "")
            QMessageBox.information(self, "Import", 
                f"Imported {count} signal definitions from:\n{filename}\n\nCAN IDs with signals: {ids_str}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to import signals:\n{str(e)}")
    
    def _import_csv_signals(self, filename: str) -> int:
        """Import signals from CSV (CAN ID,CAN Data Point,Signal name,Bit start,Bit length,Factor,Unit)"""
        count = 0
        with open(filename, 'r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            
            for row in reader:
                if len(row) < 5:
                    continue
                try:
                    # Skip undefined/reserved signals
                    data_point = row[1].strip() if len(row) > 1 else ''
                    if data_point.lower().startswith('undef') or not data_point:
                        continue
                    
                    msg_id = int(row[0].strip().replace('0x', ''), 16)
                    signal_name = row[2].strip() if len(row) > 2 else data_point
                    
                    # Parse factor - handle non-numeric values like '—'
                    factor = 1.0
                    if len(row) > 5 and row[5].strip():
                        try:
                            factor = float(row[5].strip())
                        except ValueError:
                            factor = 1.0
                    
                    signal = {
                        'name': signal_name[:12],
                        'bit_start': int(row[3]),
                        'bit_length': int(row[4]),
                        'factor': factor,
                        'unit': row[6].strip() if len(row) > 6 and row[6] != '—' else ''
                    }
                    
                    if msg_id not in self.signal_database:
                        self.signal_database[msg_id] = []
                    self.signal_database[msg_id].append(signal)
                    count += 1
                except (ValueError, IndexError):
                    continue
        return count
    
    def _import_md_signals(self, filename: str) -> int:
        """Import signals from Notion-exported Markdown table"""
        count = 0
        current_can_id = None
        
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for line in lines:
            line = line.strip()
            
            # Look for CAN ID in headers like "### GET_SOC_1 (0x18F81280)"
            if line.startswith('###') and '(0x' in line:
                try:
                    hex_start = line.find('(0x') + 3
                    hex_end = line.find(')', hex_start)
                    if hex_end > hex_start:
                        current_can_id = int(line[hex_start:hex_end], 16)
                except ValueError:
                    current_can_id = None
                continue
            
            if not line.startswith('|') or '---' in line or current_can_id is None:
                continue
            
            parts = [p.strip().replace('**', '') for p in line.split('|')[1:-1]]
            if len(parts) < 4:
                continue
            
            # Skip header rows
            if 'Signal' in parts[0] or 'Variable' in parts[0] or 'name' in parts[0].lower():
                continue
            
            try:
                name = parts[0].strip()
                if not name or name.startswith('Reserve') or name.startswith('---'):
                    continue
                
                # Parse bit_start - handle both numeric and non-numeric
                bit_start_str = parts[2].strip()
                try:
                    bit_start = int(bit_start_str)
                except ValueError:
                    continue
                
                # Parse bit_length
                bit_length_str = parts[3].strip()
                try:
                    bit_length = int(bit_length_str)
                except ValueError:
                    bit_length = 8
                
                # Parse factor
                factor = 1.0
                if len(parts) > 4 and parts[4].strip():
                    try:
                        factor = float(parts[4].strip())
                    except ValueError:
                        factor = 1.0
                
                # Parse unit
                unit = parts[5].strip() if len(parts) > 5 else ''
                # Clean up unit (remove ? and extra chars)
                unit = unit.replace('?', '').strip()
                
                if current_can_id not in self.signal_database:
                    self.signal_database[current_can_id] = []
                self.signal_database[current_can_id].append({
                    'name': name[:15], 'bit_start': bit_start,
                    'bit_length': bit_length, 'factor': factor, 'unit': unit
                })
                count += 1
            except (ValueError, IndexError):
                continue
        
        return count
    
    def _process_csv_row(self, row: list) -> bool:
        """Process a single CSV row for ID database"""
        if len(row) < 2:
            return False
        try:
            id_str = row[0].strip().replace('0x', '').replace('h', '')
            msg_id = int(id_str, 16)
            name = row[1].strip()
            if msg_id and name:
                self.id_database[msg_id] = name
                return True
        except (ValueError, IndexError):
            pass
        return False
    
    # === Header Click for HEX/Decimal/Decoded Toggle ===
    
    def _on_header_clicked(self, column: int):
        """Handle header click - toggle display mode on Data column"""
        if column == 5:  # Data column
            # Cycle through modes: hex -> decimal -> decoded -> hex
            if self.display_mode == 'hex':
                self.display_mode = 'decimal'
            elif self.display_mode == 'decimal':
                self.display_mode = 'decoded'
            else:
                self.display_mode = 'hex'
            
            # Update header text to show current mode
            mode_text = {'hex': 'Data (HEX)', 'decimal': 'Data (Decimal)', 'decoded': 'Data (Decoded)'}[self.display_mode]
            headers = ["Timestamp", "CAN-ID", "Name", "Type", "Length", 
                      mode_text, "Cycle Time", "Count"]
            self.receive_table.setHorizontalHeaderLabels(headers)
            self._update_receive_table()
            self._save_settings()
    
    def _decode_signals(self, msg_id: int, data: bytes) -> str:
        """Decode CAN data using signal definitions"""
        if msg_id not in self.signal_database:
            return " ".join(f"{b:02X}" for b in data)  # Fallback to HEX
        
        signals = self.signal_database[msg_id]
        parts = []
        
        for sig in signals:
            try:
                bit_start = sig.get('bit_start', 0)
                bit_length = sig.get('bit_length', 8)
                factor = sig.get('factor', 1) or 1
                unit = sig.get('unit', '').replace('�', '').replace('—', '')
                name = sig.get('name', 'Sig').replace('�', '').replace('—', '')
                
                # Extract value from bytes
                byte_start = bit_start // 8
                bit_offset = bit_start % 8
                bytes_needed = (bit_length + bit_offset + 7) // 8
                
                if byte_start + bytes_needed > len(data):
                    continue
                
                # Extract raw value (big endian)
                raw_value = 0
                for i in range(bytes_needed):
                    if byte_start + i < len(data):
                        raw_value = (raw_value << 8) | data[byte_start + i]
                
                # Apply bit mask and shift
                total_bits = bytes_needed * 8
                shift = total_bits - bit_offset - bit_length
                if shift >= 0:
                    raw_value = (raw_value >> shift) & ((1 << bit_length) - 1)
                
                # Apply factor
                value = raw_value * factor
                
                # Format output - shorter names for display
                short_name = name[:8]  # Truncate to 8 chars for compact display
                if factor != 1 and factor != 0:
                    parts.append(f"{short_name}:{value:.1f}{unit}")
                else:
                    parts.append(f"{short_name}:{int(value)}{unit}")
                    
            except Exception:
                continue
        
        if parts:
            return " ".join(parts)
        else:
            return " ".join(f"{b:02X}" for b in data)  # Fallback
    
    # === Edit Rule ===
    
    def _edit_rule(self):
        """Edit selected response rule"""
        row = self.rules_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Edit Rule", "Please select a rule to edit.")
            return
        
        rules = self.can_manager.get_response_rules()
        if row >= len(rules):
            return
        
        # Get original rule
        original_rule = self.can_manager._response_rules[row]
        
        dialog = AddRuleDialog(self, original_rule)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_rule = dialog.get_validated_rule()
            if new_rule:
                # Update rule using proper method
                self.can_manager.update_response_rule(row, new_rule)
                self._update_rules_table()


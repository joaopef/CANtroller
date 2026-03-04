"""
Main Window - CANtroller application interface
"""
import time
import json
import os
import sys
from datetime import datetime
from typing import Dict, Optional, List
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QToolBar, QStatusBar, QLabel,
    QComboBox, QPushButton, QGroupBox, QHeaderView, QMessageBox,
    QMenu, QMenuBar, QTabWidget, QFileDialog, QApplication,
    QProgressBar, QGridLayout, QCheckBox, QLineEdit, QFrame
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent
import can

from can_manager import CANManager, ResponseRule, TransmitMessage
from simulator import SimulationEngine, TripProfileGenerator
from config_manager import ConfigManager
from widgets.hex_inputs import HexDataLineEdit, HexByteLineEdit
from dialogs.rule_dialog import AddRuleDialog
from dialogs.transmit_dialog import NewTransmitMessageDialog

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
        # NOTE: message_received signal no longer used — using batch RX timer instead
        self.can_manager.message_sent.connect(self._on_message_sent)
        self.can_manager.connection_changed.connect(self._on_connection_changed)
        self.can_manager.error_occurred.connect(self._on_error)
        self.can_manager.status_updated.connect(self._on_status_updated)
        
        # Config Manager (handles save/load/import/export)
        self.config_mgr = ConfigManager(self.can_manager)
        
        # Message tracking
        self.receive_messages: Dict[int, dict] = {}
        self.transmit_count: Dict[int, int] = {}
        
        # Local counters for status bar
        self.local_rx_count = 0
        self.local_tx_count = 0
        
        # Filter
        self.filter_text = ""
        
        # Display mode: 'hex', 'decimal', or 'decoded'
        self.display_mode = 'hex'
        
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
        
        # RX batch processing timer (~30 FPS)
        self.rx_timer = QTimer()
        self.rx_timer.timeout.connect(self._process_rx_batch)
        self.rx_timer.start(33)
        
        # Status update timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status_bar)
        self.status_timer.start(250)
        
        # Load settings and auto-open last file
        self._load_settings()
        self._update_window_title()
        
        # Simulation engine (completely separate from transmit messages)
        self.sim_engine = SimulationEngine(self.can_manager)
        self.sim_engine.progress_changed.connect(self._on_sim_progress)
        self.sim_engine.data_updated.connect(self._on_sim_data_updated)
        self.sim_engine.simulation_finished.connect(self._on_sim_finished)
        self.sim_engine.simulation_started.connect(self._on_sim_started)
        self.sim_engine.status_message.connect(self._on_sim_status)
    
    # === Property delegators to ConfigManager ===
    @property
    def id_database(self):
        return self.config_mgr.id_database
    @id_database.setter
    def id_database(self, val):
        self.config_mgr.id_database = val
    
    @property
    def signal_database(self):
        return self.config_mgr.signal_database
    @signal_database.setter
    def signal_database(self, val):
        self.config_mgr.signal_database = val
    
    @property
    def name_to_id(self):
        return self.config_mgr.name_to_id
    @name_to_id.setter
    def name_to_id(self, val):
        self.config_mgr.name_to_id = val
    
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
        
        # --- Tab 3: Simulation ---
        sim_widget = QWidget()
        sim_layout = QVBoxLayout(sim_widget)
        sim_layout.setContentsMargins(5, 5, 5, 5)
        
        # Top controls row
        sim_controls = QHBoxLayout()
        
        # Profile selector
        sim_controls.addWidget(QLabel("Profile:"))
        self.sim_profile_combo = QComboBox()
        self.sim_profile_combo.setMinimumWidth(200)
        self._available_profiles = TripProfileGenerator.get_available_profiles()
        for p in self._available_profiles:
            self.sim_profile_combo.addItem(p['name'])
        sim_controls.addWidget(self.sim_profile_combo)
        
        sim_controls.addWidget(QLabel("  Speed:"))
        self.sim_speed_combo = QComboBox()
        self.sim_speed_combo.addItems(["1x", "2x", "5x", "10x", "20x", "50x"])
        self.sim_speed_combo.setCurrentIndex(0)
        self.sim_speed_combo.currentTextChanged.connect(self._on_sim_speed_changed)
        sim_controls.addWidget(self.sim_speed_combo)
        
        self.sim_import_btn = QPushButton("📂 Import CSV...")
        self.sim_import_btn.clicked.connect(self._sim_import_csv)
        sim_controls.addWidget(self.sim_import_btn)
        
        sim_controls.addStretch()
        
        # Start / Pause / Stop buttons
        self.sim_start_btn = QPushButton("▶ Start")
        self.sim_start_btn.clicked.connect(self._sim_start)
        self.sim_start_btn.setStyleSheet("QPushButton { background-color: #4CAF50; padding: 6px 18px; font-weight: bold; } QPushButton:hover { background-color: #66BB6A; }")
        sim_controls.addWidget(self.sim_start_btn)
        
        self.sim_pause_btn = QPushButton("⏸ Pause")
        self.sim_pause_btn.clicked.connect(self._sim_pause)
        self.sim_pause_btn.setEnabled(False)
        sim_controls.addWidget(self.sim_pause_btn)
        
        self.sim_stop_btn = QPushButton("⏹ Stop")
        self.sim_stop_btn.clicked.connect(self._sim_stop)
        self.sim_stop_btn.setEnabled(False)
        self.sim_stop_btn.setStyleSheet("QPushButton { background-color: #f44336; padding: 6px 18px; } QPushButton:hover { background-color: #ef5350; }")
        sim_controls.addWidget(self.sim_stop_btn)
        
        sim_layout.addLayout(sim_controls)
        
        # Progress bar
        self.sim_progress = QProgressBar()
        self.sim_progress.setRange(0, 100)
        self.sim_progress.setValue(0)
        self.sim_progress.setTextVisible(True)
        self.sim_progress.setFormat("Ready")
        self.sim_progress.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; background-color: #1e1e1e; color: #e0e0e0; height: 20px; }
            QProgressBar::chunk { background-color: #4CAF50; border-radius: 2px; }
        """)
        sim_layout.addWidget(self.sim_progress)
        
        # Live data display
        live_data_group = QGroupBox("Live Simulation Data")
        live_grid = QGridLayout(live_data_group)
        live_grid.setContentsMargins(10, 10, 10, 5)
        
        # Row 0: BMS data
        live_grid.addWidget(QLabel("🔋 Voltage:"), 0, 0)
        self.sim_voltage_label = QLabel("---")
        self.sim_voltage_label.setStyleSheet("color: #4fc3f7; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_voltage_label, 0, 1)
        
        live_grid.addWidget(QLabel("⚡ Current:"), 0, 2)
        self.sim_current_label = QLabel("---")
        self.sim_current_label.setStyleSheet("color: #ffb74d; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_current_label, 0, 3)
        
        live_grid.addWidget(QLabel("🔋 SOC:"), 0, 4)
        self.sim_soc_label = QLabel("---")
        self.sim_soc_label.setStyleSheet("color: #81c784; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_soc_label, 0, 5)
        
        # Row 1: MCU data
        live_grid.addWidget(QLabel("🏎️ Speed:"), 1, 0)
        self.sim_speed_label = QLabel("---")
        self.sim_speed_label.setStyleSheet("color: #ce93d8; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_speed_label, 1, 1)
        
        live_grid.addWidget(QLabel("📏 Trip:"), 1, 2)
        self.sim_mileage_label = QLabel("---")
        self.sim_mileage_label.setStyleSheet("color: #a5d6a7; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_mileage_label, 1, 3)
        
        live_grid.addWidget(QLabel("⚙️ Gear:"), 1, 4)
        self.sim_gear_label = QLabel("---")
        self.sim_gear_label.setStyleSheet("color: #90caf9; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_gear_label, 1, 5)
        
        # Row 2: Temperature data
        live_grid.addWidget(QLabel("🌡️ Temperature:"), 2, 0)
        self.sim_temp_label = QLabel("---")
        self.sim_temp_label.setStyleSheet("color: #ef9a9a; font-weight: bold; font-size: 13px;")
        live_grid.addWidget(self.sim_temp_label, 2, 1)
        
        sim_layout.addWidget(live_data_group)
        
        self.transmit_tabs.addTab(sim_widget, "🔄 Simulation")
        
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
        
        # Simulation menu
        sim_menu = menubar.addMenu("&Simulation")
        
        sim_start_action = QAction("▶ &Start Simulation", self)
        sim_start_action.triggered.connect(self._sim_start)
        sim_menu.addAction(sim_start_action)
        
        sim_pause_action = QAction("⏸ &Pause Simulation", self)
        sim_pause_action.triggered.connect(self._sim_pause)
        sim_menu.addAction(sim_pause_action)
        
        sim_stop_action = QAction("⏹ S&top Simulation", self)
        sim_stop_action.triggered.connect(self._sim_stop)
        sim_menu.addAction(sim_stop_action)
        
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
    
    def _process_rx_batch(self):
        """Process buffered RX messages in batch (~30 FPS)"""
        msgs = self.can_manager.drain_rx_buffer()
        if not msgs:
            return
        for msg in msgs:
            self._on_message_received_internal(msg)
        self._update_receive_table()
    
    def _on_message_received_internal(self, msg):
        """Handle a single received CAN message (called from batch processor)"""
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
            if self._load_config_file(filename):
                QMessageBox.information(self, "Success", f"Configuration loaded from:\n{filename}")
            else:
                QMessageBox.warning(self, "Error", f"Failed to load configuration:\n{filename}")
    
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
            self.config_mgr.save_config(
                filename,
                self.channel_combo.currentText(),
                self.bitrate_combo.currentText()
            )
            self.current_file = filename
            self._update_window_title()
            self._save_settings()
            QMessageBox.information(self, "Success", f"Configuration saved to:\n{filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save configuration:\n{str(e)}")
    
    # === Settings Persistence ===
    
    def _load_settings(self):
        """Load application settings from file"""
        result = self.config_mgr.load_settings(SETTINGS_FILE)
        self.display_mode = result['display_mode']
        last_file = result.get('last_file')
        if last_file and os.path.exists(last_file):
            self._load_config_file(last_file)
    
    def _save_settings(self):
        """Save application settings to file"""
        self.config_mgr.save_settings(SETTINGS_FILE, self.current_file, self.display_mode)
    
    def _load_config_file(self, filename: str):
        """Load configuration from file (internal helper)"""
        try:
            settings = self.config_mgr.load_config(filename)
            
            # Apply connection settings if present
            if settings:
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
            self, "Export Logs", f"can_log.{format_type}",
            extensions.get(format_type, 'All Files (*)')
        )
        if not filename:
            return
        
        try:
            self.config_mgr.export_logs(filename, format_type, self.receive_messages)
            QMessageBox.information(self, "Export", f"Logs exported to:\n{filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to export logs:\n{str(e)}")
    
    # === Import CAN Database ===
    
    def _import_id_database(self):
        """Import CAN ID names from CSV or MD file"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import CAN ID Database", "",
            "All Supported (*.csv *.md);;CSV Files (*.csv);;Markdown Files (*.md);;All Files (*)"
        )
        if not filename:
            return
        
        try:
            if filename.endswith('.md'):
                count = self.config_mgr.import_md_blocks(filename)
            else:
                count = self.config_mgr.import_csv_blocks(filename)
            
            self._save_settings()
            self._update_receive_table()
            QMessageBox.information(self, "Import", f"Imported {count} CAN ID entries from:\n{filename}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to import database:\n{str(e)}")
    
    def _import_signal_database(self):
        """Import signal definitions from CSV or MD file"""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import Signal Definitions", "",
            "All Supported (*.csv *.md);;Markdown Files (*.md);;CSV Files (*.csv);;All Files (*)"
        )
        if not filename:
            return
        
        try:
            if filename.endswith('.md'):
                count = self.config_mgr.import_md_signals(filename)
            else:
                count = self.config_mgr.import_csv_signals(filename)
            self._save_settings()
            can_ids = [f"0x{cid:08X}" for cid in self.signal_database.keys()]
            ids_str = ", ".join(can_ids[:5]) + ("..." if len(can_ids) > 5 else "")
            QMessageBox.information(self, "Import", 
                f"Imported {count} signal definitions from:\n{filename}\n\nCAN IDs with signals: {ids_str}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to import signals:\n{str(e)}")
    
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

    # === Simulation Controls ===
    
    def _sim_start(self):
        """Start or resume the simulation"""
        if self.sim_engine.is_paused:
            self.sim_engine.start()
            return
        
        # Generate a new profile from the selected option
        idx = self.sim_profile_combo.currentIndex()
        if idx < 0 or idx >= len(self._available_profiles):
            QMessageBox.warning(self, "Simulation", "Please select a profile.")
            return
        
        profile_info = self._available_profiles[idx]
        profile = profile_info.get('_loaded_profile')
        if not profile and profile_info.get('generator'):
            profile = profile_info['generator'](**profile_info['kwargs'])
        
        self.sim_engine.load_profile(profile)
        
        # Apply current speed
        self._on_sim_speed_changed(self.sim_speed_combo.currentText())
        
        if not self.sim_engine.start():
            QMessageBox.warning(self, "Simulation", 
                "Cannot start simulation.\nMake sure CAN bus is connected.")
    
    def _sim_import_csv(self):
        """Import a CSV file as a trip profile"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Import Trip CSV", "",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not filepath:
            return
        
        try:
            profile = TripProfileGenerator.load_csv_profile(filepath)
            # Add to combo and select it
            import os
            name = f"CSV: {os.path.basename(filepath)}"
            self._available_profiles.append({
                'name': name,
                'generator': None,  # Already generated
                'kwargs': {},
                'description': f'Real trip data ({profile.point_count} points, {profile.duration_min:.0f} min)',
                '_loaded_profile': profile  # Store the loaded profile
            })
            self.sim_profile_combo.addItem(name)
            self.sim_profile_combo.setCurrentIndex(self.sim_profile_combo.count() - 1)
            
            QMessageBox.information(self, "CSV Imported",
                f"Loaded trip profile:\n"
                f"• {profile.point_count} data points\n"
                f"• Duration: {profile.duration_min:.1f} min\n"
                f"• Voltage: {profile.data_points[0].voltage_V:.1f}V → {profile.data_points[-1].voltage_V:.1f}V\n"
                f"• Max speed: {max(dp.speed_kmh for dp in profile.data_points)} km/h")
        except Exception as e:
            QMessageBox.warning(self, "Import Error", f"Failed to load CSV:\n{str(e)}")
    
    def _sim_pause(self):
        """Pause/resume the simulation"""
        if self.sim_engine.is_running:
            if self.sim_engine.is_paused:
                self.sim_engine.start()  # Resume
                self.sim_pause_btn.setText("⏸ Pause")
            else:
                self.sim_engine.pause()
                self.sim_pause_btn.setText("▶ Resume")
    
    def _sim_stop(self):
        """Stop the simulation"""
        self.sim_engine.stop()
        self._on_sim_finished()
    
    def _on_sim_speed_changed(self, text: str):
        """Handle simulation speed change"""
        try:
            speed = float(text.replace('x', ''))
            self.sim_engine.playback_speed = speed
        except ValueError:
            pass
    
    def _on_sim_started(self):
        """Handle simulation started"""
        self.sim_start_btn.setEnabled(False)
        self.sim_pause_btn.setEnabled(True)
        self.sim_stop_btn.setEnabled(True)
        self.sim_profile_combo.setEnabled(False)
        self.sim_progress.setFormat("%p% — Running")
    
    def _on_sim_progress(self, progress: int):
        """Handle simulation progress update"""
        self.sim_progress.setValue(progress)
    
    def _on_sim_data_updated(self, data: dict):
        """Handle simulation live data update"""
        self.sim_voltage_label.setText(f"{data['voltage']:.1f} V")
        self.sim_current_label.setText(f"{data['current']:.1f} A")
        self.sim_soc_label.setText(f"{data['soc']:.1f} %")
        self.sim_speed_label.setText(f"{data['speed']} km/h")
        self.sim_mileage_label.setText(f"{data['mileage']:.1f} km")
        self.sim_gear_label.setText(f"{data['gear']}")
        if 'temperature' in data:
            self.sim_temp_label.setText(f"{data['temperature']:.1f} °C")
    
    def _on_sim_finished(self):
        """Handle simulation finished"""
        self.sim_start_btn.setEnabled(True)
        self.sim_pause_btn.setEnabled(False)
        self.sim_pause_btn.setText("⏸ Pause")
        self.sim_stop_btn.setEnabled(False)
        self.sim_profile_combo.setEnabled(True)
        self.sim_progress.setFormat("Ready")
        self.sim_progress.setValue(0)
        # Reset live data
        self.sim_voltage_label.setText("---")
        self.sim_current_label.setText("---")
        self.sim_soc_label.setText("---")
        self.sim_speed_label.setText("---")
        self.sim_mileage_label.setText("---")
        self.sim_gear_label.setText("---")
        self.sim_temp_label.setText("---")
    
    def _on_sim_status(self, msg: str):
        """Handle simulation status message"""
        self.status_label.setText(msg)


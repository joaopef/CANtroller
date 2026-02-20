"""
Transmit Message Dialog - Dialog for creating/editing periodic CAN messages
"""
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QSpinBox, QCheckBox, QGroupBox, QDialogButtonBox, QMessageBox
)

from widgets.hex_inputs import HexByteLineEdit
from can_manager import TransmitMessage


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
        
        # Increment Byte (auto-counter)
        inc_group = QGroupBox("Auto-Increment")
        inc_layout = QVBoxLayout(inc_group)
        self.increment_combo = QComboBox()
        self.increment_combo.addItem("None", -1)
        for i in range(8):
            self.increment_combo.addItem(f"Byte {i}", i)
        self.increment_combo.setToolTip("Auto-increment the selected byte\non each send cycle (wraps 255→0)")
        inc_layout.addWidget(self.increment_combo)
        middle_layout.addWidget(inc_group)
        
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
            # Set increment byte if configured
            idx = self.increment_combo.findData(msg.increment_byte)
            if idx >= 0:
                self.increment_combo.setCurrentIndex(idx)
    
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
                comment=self.comment_edit.text().strip(),
                increment_byte=self.increment_combo.currentData()
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", f"Invalid hex value: {e}")
            return None
    
    def get_validated_message(self) -> Optional[TransmitMessage]:
        """Get the validated message after dialog closes"""
        return getattr(self, '_valid_msg', None)

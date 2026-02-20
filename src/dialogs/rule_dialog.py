"""
Rule Dialog - Dialog for adding/editing CAN response rules
"""
from typing import Optional
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QCheckBox, QSpinBox, 
    QComboBox, QDialogButtonBox, QMessageBox
)

from widgets.hex_inputs import HexDataLineEdit
from can_manager import ResponseRule


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
        
        # Increment Byte (auto-counter)
        self.increment_combo = QComboBox()
        self.increment_combo.addItem("None", -1)
        for i in range(8):
            self.increment_combo.addItem(f"Byte {i}", i)
        self.increment_combo.setToolTip("Auto-increment the selected byte\non each response (wraps 255→0)")
        layout.addRow("Increment Byte:", self.increment_combo)
        
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
            # Set increment byte if configured
            idx = self.increment_combo.findData(rule.increment_byte)
            if idx >= 0:
                self.increment_combo.setCurrentIndex(idx)
    
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
                comment=self.comment_edit.text().strip(),
                increment_byte=self.increment_combo.currentData()
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", str(e))
            return None
    
    def get_validated_rule(self) -> Optional[ResponseRule]:
        """Get the validated rule after dialog closes"""
        return getattr(self, '_valid_rule', None)

"""
Hex Input Widgets - Custom QLineEdits for hex data entry
"""
import re
from PyQt6.QtWidgets import QLineEdit


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

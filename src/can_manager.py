"""
CAN Manager - Handles PCAN connection and message handling
"""
import can
import threading
import time
from typing import Callable, Optional, List, Dict
from dataclasses import dataclass, field
from PyQt6.QtCore import QObject, pyqtSignal, QTimer


@dataclass
class ResponseRule:
    """Rule for auto-responding to CAN messages"""
    trigger_id: int
    response_id: int
    response_data: List[int]
    is_extended: bool = True
    delay_ms: int = 0
    comment: str = ""
    enabled: bool = True
    increment_byte: int = -1  # -1 = disabled, 0-7 = byte index to auto-increment


@dataclass
class TransmitMessage:
    """Message for periodic transmission"""
    msg_id: int
    data: List[int]
    is_extended: bool = True
    cycle_time_ms: int = 100
    is_paused: bool = False
    comment: str = ""
    count: int = 0
    increment_byte: int = -1  # -1 = disabled, 0-7 = byte index to auto-increment
    # Internal state
    _timer: Optional[QTimer] = field(default=None, repr=False)


class CANManager(QObject):
    """Manages PCAN-USB connection and CAN message handling"""
    
    # Signals for thread-safe GUI updates
    message_received = pyqtSignal(object)  # can.Message
    connection_changed = pyqtSignal(bool, str)  # connected, status_text
    message_sent = pyqtSignal(object)  # can.Message
    error_occurred = pyqtSignal(str)
    status_updated = pyqtSignal(dict)  # status info dict
    
    BITRATES = {
        "125 kbit/s": 125000,
        "250 kbit/s": 250000,
        "500 kbit/s": 500000,
        "1 Mbit/s": 1000000,
    }
    
    CHANNELS = [
        "PCAN_USBBUS1",
        "PCAN_USBBUS2",
        "PCAN_USBBUS3",
        "PCAN_USBBUS4",
    ]
    
    def __init__(self):
        super().__init__()
        self.bus: Optional[can.Bus] = None
        self._running = False
        self._receive_thread: Optional[threading.Thread] = None
        self._response_rules: List[ResponseRule] = []
        self._transmit_messages: List[TransmitMessage] = []
        self._response_mode_enabled = False
        self._paused = False
        self._channel = ""
        self._bitrate = 0
        
        # Statistics
        self._rx_count = 0
        self._tx_count = 0
        self._error_count = 0
        self._overruns = 0
        
    @property
    def is_connected(self) -> bool:
        return self.bus is not None
    
    @property
    def response_mode_enabled(self) -> bool:
        return self._response_mode_enabled
    
    @response_mode_enabled.setter
    def response_mode_enabled(self, value: bool):
        self._response_mode_enabled = value
        
    @property
    def paused(self) -> bool:
        return self._paused
    
    @paused.setter
    def paused(self, value: bool):
        self._paused = value
        
    def connect(self, channel: str, bitrate: int) -> bool:
        """Connect to PCAN device"""
        try:
            self.bus = can.interface.Bus(
                bustype='pcan',
                channel=channel,
                bitrate=bitrate
            )
            self._channel = channel
            self._bitrate = bitrate
            self._running = True
            self._rx_count = 0
            self._tx_count = 0
            self._error_count = 0
            self._overruns = 0
            
            self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._receive_thread.start()
            
            status = f"Connected to {channel} @ {bitrate // 1000} kbit/s"
            self.connection_changed.emit(True, status)
            self._emit_status()
            return True
            
        except Exception as e:
            error_msg = f"Connection failed: {str(e)}"
            self.error_occurred.emit(error_msg)
            self.connection_changed.emit(False, error_msg)
            return False
    
    def disconnect(self):
        """Disconnect from PCAN device"""
        # Stop all periodic transmissions
        self.stop_all_transmissions()
        
        self._running = False
        if self._receive_thread:
            self._receive_thread.join(timeout=2.0)
        if self.bus:
            self.bus.shutdown()
            self.bus = None
        self.connection_changed.emit(False, "Disconnected")
    
    def send_message(self, arbitration_id: int, data: List[int], is_extended: bool = True) -> bool:
        """Send a CAN message"""
        if not self.bus:
            return False
            
        try:
            msg = can.Message(
                arbitration_id=arbitration_id,
                data=data,
                is_extended_id=is_extended
            )
            self.bus.send(msg)
            self._tx_count += 1
            self.message_sent.emit(msg)
            self._emit_status()
            return True
        except Exception as e:
            self._error_count += 1
            self.error_occurred.emit(f"Send failed: {str(e)}")
            self._emit_status()
            return False
    
    def _emit_status(self):
        """Emit current status"""
        status = {
            'channel': self._channel,
            'bitrate': self._bitrate,
            'rx_count': self._rx_count,
            'tx_count': self._tx_count,
            'errors': self._error_count,
            'overruns': self._overruns,
            'status': 'OK' if self.is_connected else 'Disconnected'
        }
        self.status_updated.emit(status)
    
    # === Response Rules ===
    
    def add_response_rule(self, rule: ResponseRule):
        """Add a response rule"""
        self._response_rules.append(rule)
    
    def remove_response_rule(self, index: int):
        """Remove a response rule by index"""
        if 0 <= index < len(self._response_rules):
            self._response_rules.pop(index)
    
    def get_response_rules(self) -> List[ResponseRule]:
        """Get all response rules"""
        return self._response_rules.copy()
    
    def clear_response_rules(self):
        """Clear all response rules"""
        self._response_rules.clear()
    
    def update_response_rule(self, index: int, rule: ResponseRule):
        """Update a response rule at the given index"""
        if 0 <= index < len(self._response_rules):
            self._response_rules[index] = rule
    
    # === Transmit Messages (Periodic) ===
    
    def add_transmit_message(self, msg: TransmitMessage):
        """Add a periodic transmit message"""
        self._transmit_messages.append(msg)
        if not msg.is_paused and self.is_connected:
            self._start_transmit_timer(msg)
    
    def remove_transmit_message(self, index: int):
        """Remove a transmit message by index"""
        if 0 <= index < len(self._transmit_messages):
            msg = self._transmit_messages[index]
            if msg._timer:
                msg._timer.stop()
                msg._timer = None
            self._transmit_messages.pop(index)
    
    def get_transmit_messages(self) -> List[TransmitMessage]:
        """Get all transmit messages"""
        return self._transmit_messages.copy()
    
    def toggle_transmit_message(self, index: int):
        """Toggle pause state of a transmit message"""
        if 0 <= index < len(self._transmit_messages):
            msg = self._transmit_messages[index]
            msg.is_paused = not msg.is_paused
            
            if msg.is_paused:
                if msg._timer:
                    msg._timer.stop()
            else:
                if self.is_connected:
                    self._start_transmit_timer(msg)
    
    def send_transmit_message_once(self, index: int):
        """Send a transmit message once (manual trigger)"""
        if 0 <= index < len(self._transmit_messages):
            msg = self._transmit_messages[index]
            self.send_message(msg.msg_id, msg.data, msg.is_extended)
            msg.count += 1
    
    def _start_transmit_timer(self, msg: TransmitMessage):
        """Start periodic transmission for a message"""
        if msg._timer:
            msg._timer.stop()
        
        msg._timer = QTimer()
        msg._timer.timeout.connect(lambda: self._send_periodic(msg))
        msg._timer.start(msg.cycle_time_ms)
    
    def _send_periodic(self, msg: TransmitMessage):
        """Send a periodic message, with optional byte auto-increment"""
        if not msg.is_paused and self.is_connected:
            # Auto-increment the chosen byte before sending
            if 0 <= msg.increment_byte < len(msg.data):
                msg.data[msg.increment_byte] = (msg.data[msg.increment_byte] + 1) & 0xFF
            self.send_message(msg.msg_id, msg.data, msg.is_extended)
            msg.count += 1
    
    def stop_all_transmissions(self):
        """Stop all periodic transmissions"""
        for msg in self._transmit_messages:
            if msg._timer:
                msg._timer.stop()
                msg._timer = None
    
    def start_all_transmissions(self):
        """Start all non-paused transmissions"""
        for msg in self._transmit_messages:
            if not msg.is_paused:
                self._start_transmit_timer(msg)
    
    def clear_transmit_messages(self):
        """Clear all transmit messages"""
        self.stop_all_transmissions()
        self._transmit_messages.clear()
    
    # === Receive Loop ===
    
    def _receive_loop(self):
        """Background thread for receiving CAN messages"""
        while self._running and self.bus:
            try:
                msg = self.bus.recv(timeout=0.1)
                if msg and not self._paused:
                    self._rx_count += 1
                    self.message_received.emit(msg)
                    
                    # Check for auto-response
                    if self._response_mode_enabled:
                        self._check_and_respond(msg)
                        
            except Exception as e:
                if self._running:
                    self._error_count += 1
                    self.error_occurred.emit(f"Receive error: {str(e)}")
    
    def _check_and_respond(self, received_msg: can.Message):
        """Check if received message triggers a response"""
        for rule in self._response_rules:
            if not rule.enabled:
                continue
                
            if received_msg.arbitration_id == rule.trigger_id:
                # Apply delay if specified
                if rule.delay_ms > 0:
                    time.sleep(rule.delay_ms / 1000.0)
                
                # Auto-increment chosen byte before responding
                if 0 <= rule.increment_byte < len(rule.response_data):
                    rule.response_data[rule.increment_byte] = (
                        rule.response_data[rule.increment_byte] + 1
                    ) & 0xFF
                
                # Send response
                self.send_message(
                    rule.response_id,
                    rule.response_data,
                    rule.is_extended
                )

"""
Labeled Logger — Thread-safe CSV logger for labeled CAN bus datasets.

Logs every TX and RX frame with a label (normal, dos, injection, fuzzing,
replay, masquerade, suspension) so the resulting CSV can be used directly
as training data for an Intrusion Detection System.
"""
import csv
import logging
import os
import threading
import time
from collections import deque
from itertools import count as _icount
from typing import Optional

log = logging.getLogger('cantroller.labeled_logger')


class LabeledLogger:
    """
    Thread-safe labeled CAN frame logger.

    Writes to a CSV file with columns:
        timestamp_us, can_id, is_extended, dlc, data_hex, direction, label, attack_subtype

    Usage:
        logger = LabeledLogger()
        logger.start("dataset_001.csv")
        logger.log_rx(msg)                          # normal RX
        logger.log_tx(0x000, [0]*8, False, "dos", "flood")  # attack TX
        logger.stop()
    """

    HEADER = [
        "timestamp_us", "can_id", "is_extended", "dlc",
        "data_hex", "direction", "label", "attack_subtype"
    ]

    def __init__(self, buffer_size: int = 5000):
        self._file = None
        self._writer = None
        self._lock = threading.Lock()
        self._buffer: deque = deque(maxlen=buffer_size)
        self._running = False
        self._flush_thread: Optional[threading.Thread] = None
        self._filepath: Optional[str] = None
        # Current label state — set by the attack generator
        self._current_label = "normal"
        self._current_subtype = ""
        # Thread-safe counters
        self._rx_count_lock = threading.Lock()
        self._tx_count_lock = threading.Lock()
        self.rx_count = 0
        self.tx_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def filepath(self) -> Optional[str]:
        return self._filepath

    @property
    def total_count(self) -> int:
        return self.rx_count + self.tx_count

    def set_label(self, label: str, subtype: str = ""):
        """Set the current label applied to all subsequent logged frames."""
        self._current_label = label
        self._current_subtype = subtype

    def reset_label(self):
        """Reset label to normal."""
        self._current_label = "normal"
        self._current_subtype = ""

    def start(self, filepath: str):
        """Open the CSV file and begin logging."""
        if self._running:
            self.stop()

        self._filepath = filepath
        self._file = open(filepath, 'w', newline='', encoding='utf-8')
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.HEADER)
        self._file.flush()
        self._running = True
        self.rx_count = 0
        self.tx_count = 0
        log.info('Logger started → %s', filepath)

        # Background flush thread (writes buffered rows every 500ms)
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def stop(self):
        """Flush remaining data and close the file."""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=2.0)
            self._flush_thread = None
        self._flush_buffer()
        if self._file:
            self._file.close()
            self._file = None
        self._writer = None

    def log_rx(self, msg, label: Optional[str] = None, subtype: Optional[str] = None):
        """
        Log a received CAN message (from can.Message).
        If label/subtype not provided, uses the current label state.
        """
        if not self._running:
            return
        ts = int(time.time() * 1_000_000) if msg.timestamp is None else int(msg.timestamp * 1_000_000)
        data_hex = ' '.join(f'{b:02X}' for b in msg.data)
        row = [
            ts,
            f'0x{msg.arbitration_id:08X}',
            1 if msg.is_extended_id else 0,
            msg.dlc,
            data_hex,
            "RX",
            label if label else self._current_label,
            subtype if subtype else self._current_subtype,
        ]
        self._buffer.append(row)
        with self._rx_count_lock:
            self.rx_count += 1

    def log_tx(self, arb_id: int, data: list, is_extended: bool,
               label: Optional[str] = None, subtype: Optional[str] = None):
        """
        Log a transmitted CAN frame.
        If label/subtype not provided, uses the current label state.
        """
        if not self._running:
            return
        ts = int(time.time() * 1_000_000)
        data_hex = ' '.join(f'{b:02X}' for b in data)
        row = [
            ts,
            f'0x{arb_id:08X}',
            1 if is_extended else 0,
            len(data),
            data_hex,
            "TX",
            label if label else self._current_label,
            subtype if subtype else self._current_subtype,
        ]
        self._buffer.append(row)
        with self._tx_count_lock:
            self.tx_count += 1

    def _flush_loop(self):
        """Background thread: periodically flush buffer to disk."""
        while self._running:
            time.sleep(0.5)
            self._flush_buffer()

    def _flush_buffer(self):
        """Write all buffered rows to CSV."""
        if not self._writer:
            return
        rows = []
        while self._buffer:
            try:
                rows.append(self._buffer.popleft())
            except IndexError:
                break
        if rows:
            with self._lock:
                if self._writer and self._file:
                    self._writer.writerows(rows)
                    self._file.flush()

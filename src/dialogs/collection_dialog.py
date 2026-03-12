"""
Dataset Collection Configuration Dialog
Allows the user to configure per-attack durations, rounds, and normal intervals.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QDialogButtonBox, QPushButton
)
from PyQt6.QtCore import Qt


# Default durations in seconds
ATTACK_DEFAULTS = {
    'dos':        {'enabled': True, 'duration': 15},
    'injection':  {'enabled': True, 'duration': 30},
    'fuzzing':    {'enabled': True, 'duration': 20},
    'replay':     {'enabled': True, 'duration': 30},
    'suspension': {'enabled': True, 'duration': 30},
    'masquerade': {'enabled': True, 'duration': 45},
}

ATTACK_LABELS = {
    'dos':        'DoS — Bus Flooding',
    'injection':  'Injection — Forged Frames',
    'fuzzing':    'Fuzzing — Random Traffic',
    'replay':     'Replay — Record & Retransmit',
    'suspension': 'Suspension — Message Absence',
    'masquerade': 'Masquerade — Suppress + Impersonate',
}


class CollectionConfigDialog(QDialog):
    """Configuration dialog for automated dataset collection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dataset Collection Settings")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)

        # Global settings
        global_group = QGroupBox("Global Settings")
        g_layout = QGridLayout(global_group)

        g_layout.addWidget(QLabel("Normal traffic between attacks (s):"), 0, 0)
        self.normal_dur_edit = QLineEdit("60")
        self.normal_dur_edit.setMaximumWidth(80)
        g_layout.addWidget(self.normal_dur_edit, 0, 1)

        g_layout.addWidget(QLabel("Number of rounds:"), 1, 0)
        self.rounds_spin = QSpinBox()
        self.rounds_spin.setRange(1, 50)
        self.rounds_spin.setValue(1)
        self.rounds_spin.setMaximumWidth(80)
        g_layout.addWidget(self.rounds_spin, 1, 1)

        layout.addWidget(global_group)

        # Per-attack settings
        atk_group = QGroupBox("Attack Durations (seconds)")
        a_layout = QGridLayout(atk_group)
        a_layout.addWidget(QLabel("Enabled"), 0, 0)
        a_layout.addWidget(QLabel("Attack Type"), 0, 1)
        a_layout.addWidget(QLabel("Duration (s)"), 0, 2)

        self._attack_widgets = {}
        row = 1
        for key in ['dos', 'injection', 'fuzzing', 'replay', 'suspension', 'masquerade']:
            defaults = ATTACK_DEFAULTS[key]
            cb = QCheckBox()
            cb.setChecked(defaults['enabled'])
            a_layout.addWidget(cb, row, 0, alignment=Qt.AlignmentFlag.AlignCenter)

            label = QLabel(ATTACK_LABELS[key])
            a_layout.addWidget(label, row, 1)

            dur_edit = QLineEdit(str(defaults['duration']))
            dur_edit.setMaximumWidth(80)
            a_layout.addWidget(dur_edit, row, 2)

            self._attack_widgets[key] = (cb, dur_edit)
            row += 1

        layout.addWidget(atk_group)

        # OK / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_config(self) -> dict:
        """Return the collection configuration as a dict."""
        try:
            normal_dur = max(1.0, float(self.normal_dur_edit.text()))
        except ValueError:
            normal_dur = 60.0

        attacks = {}
        for key, (cb, dur_edit) in self._attack_widgets.items():
            try:
                dur = max(1.0, float(dur_edit.text()))
            except ValueError:
                dur = ATTACK_DEFAULTS[key]['duration']
            attacks[key] = {
                'enabled': cb.isChecked(),
                'duration': dur,
            }

        return {
            'normal_duration_s': normal_dur,
            'rounds': self.rounds_spin.value(),
            'attacks': attacks,
        }

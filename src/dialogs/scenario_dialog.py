"""
Scenario Builder Dialog — Define and run custom test sequences.

A scenario is a list of steps that execute sequentially:
  - 'normal'     — record normal traffic for a duration
  - 'dos'        — run DoS attack
  - 'injection'  — inject forged frames
  - 'fuzzing'    — fuzz random frames
  - 'replay'     — record and replay
  - 'masquerade' — suppress + impersonate
  - 'suspension' — suppress an ID
  - 'pause'      — idle wait (no logging label change)

Scenarios can be saved/loaded as JSON files.
"""
import json
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QComboBox, QPushButton, QTableWidget,
    QTableWidgetItem, QDialogButtonBox, QFileDialog, QHeaderView,
    QMessageBox, QSpinBox
)
from PyQt6.QtCore import Qt


STEP_TYPES = [
    "normal",
    "dos",
    "injection",
    "fuzzing",
    "replay",
    "masquerade",
    "suspension",
    "pause",
    "sim:city",
    "sim:highway",
    "sim:charge",
    "sim:wmtc_p1",
    "sim:wmtc_p1p2",
]

DEFAULT_DURATIONS = {
    "normal": 60,
    "dos": 15,
    "injection": 30,
    "fuzzing": 20,
    "replay": 30,
    "masquerade": 45,
    "suspension": 30,
    "pause": 10,
    "sim:city": 600,
    "sim:highway": 600,
    "sim:charge": 600,
    "sim:wmtc_p1": 600,
    "sim:wmtc_p1p2": 1200,
}


class ScenarioBuilderDialog(QDialog):
    """GUI for building and editing test scenarios."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scenario Builder")
        self.setMinimumSize(620, 450)
        layout = QVBoxLayout(self)

        # --- Step Table ---
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Step Type", "Duration (s)", "Notes"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        # --- Step controls ---
        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(STEP_TYPES)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        ctrl.addWidget(self.type_combo)

        ctrl.addWidget(QLabel("Duration (s):"))
        self.dur_edit = QLineEdit(str(DEFAULT_DURATIONS["normal"]))
        self.dur_edit.setMaximumWidth(70)
        ctrl.addWidget(self.dur_edit)

        ctrl.addWidget(QLabel("Notes:"))
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("optional description")
        ctrl.addWidget(self.notes_edit)

        add_btn = QPushButton("➕ Add Step")
        add_btn.clicked.connect(self._add_step)
        ctrl.addWidget(add_btn)

        layout.addLayout(ctrl)

        # --- Edit controls ---
        edit_row = QHBoxLayout()

        remove_btn = QPushButton("🗑️ Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        edit_row.addWidget(remove_btn)

        up_btn = QPushButton("⬆ Move Up")
        up_btn.clicked.connect(self._move_up)
        edit_row.addWidget(up_btn)

        down_btn = QPushButton("⬇ Move Down")
        down_btn.clicked.connect(self._move_down)
        edit_row.addWidget(down_btn)

        duplicate_btn = QPushButton("📋 Duplicate")
        duplicate_btn.clicked.connect(self._duplicate)
        edit_row.addWidget(duplicate_btn)

        edit_row.addStretch()

        # Repeat N times
        edit_row.addWidget(QLabel("Repeat scenario:"))
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 100)
        self.repeat_spin.setValue(1)
        self.repeat_spin.setMaximumWidth(60)
        edit_row.addWidget(self.repeat_spin)
        edit_row.addWidget(QLabel("time(s)"))

        layout.addLayout(edit_row)

        # --- Save / Load / Run ---
        file_row = QHBoxLayout()

        save_btn = QPushButton("💾 Save Scenario…")
        save_btn.clicked.connect(self._save_scenario)
        file_row.addWidget(save_btn)

        load_btn = QPushButton("📂 Load Scenario…")
        load_btn.clicked.connect(self._load_scenario)
        file_row.addWidget(load_btn)

        file_row.addStretch()

        # Summary label
        self.summary_label = QLabel("0 steps, ~0s total")
        self.summary_label.setStyleSheet("color: #888;")
        file_row.addWidget(self.summary_label)

        layout.addLayout(file_row)

        # OK / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("▶ Run Scenario")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_summary()

    def _on_type_changed(self, text: str):
        self.dur_edit.setText(str(DEFAULT_DURATIONS.get(text, 30)))

    def _add_step(self):
        step_type = self.type_combo.currentText()
        duration = self.dur_edit.text()
        notes = self.notes_edit.text()
        self._insert_row(self.table.rowCount(), step_type, duration, notes)
        self.notes_edit.clear()
        self._update_summary()

    def _insert_row(self, row: int, step_type: str, duration: str, notes: str):
        self.table.insertRow(row)
        type_item = QTableWidgetItem(step_type)
        type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, type_item)
        self.table.setItem(row, 1, QTableWidgetItem(str(duration)))
        self.table.setItem(row, 2, QTableWidgetItem(notes))

    def _remove_selected(self):
        rows = sorted(set(i.row() for i in self.table.selectedIndexes()), reverse=True)
        for row in rows:
            self.table.removeRow(row)
        self._update_summary()

    def _move_up(self):
        row = self.table.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self.table.setCurrentCell(row - 1, 0)

    def _move_down(self):
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount() - 1:
            return
        self._swap_rows(row, row + 1)
        self.table.setCurrentCell(row + 1, 0)

    def _swap_rows(self, a: int, b: int):
        for col in range(self.table.columnCount()):
            text_a = self.table.item(a, col).text()
            text_b = self.table.item(b, col).text()
            self.table.item(a, col).setText(text_b)
            self.table.item(b, col).setText(text_a)

    def _duplicate(self):
        row = self.table.currentRow()
        if row < 0:
            return
        step_type = self.table.item(row, 0).text()
        duration = self.table.item(row, 1).text()
        notes = self.table.item(row, 2).text()
        self._insert_row(row + 1, step_type, duration, notes)
        self._update_summary()

    def _update_summary(self):
        n = self.table.rowCount()
        total = 0
        for row in range(n):
            try:
                total += float(self.table.item(row, 1).text())
            except (ValueError, AttributeError):
                pass
        repeats = self.repeat_spin.value()
        total *= repeats
        mins = total / 60
        self.summary_label.setText(
            f"{n} steps × {repeats} = {n*repeats} total, ~{total:.0f}s ({mins:.1f} min)")

    def _save_scenario(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Scenario", "scenario.json",
            "JSON Files (*.json);;All Files (*)")
        if not filepath:
            return
        data = {
            'repeat': self.repeat_spin.value(),
            'steps': self.get_steps_raw(),
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_scenario(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Load Scenario", "",
            "JSON Files (*.json);;All Files (*)")
        if not filepath:
            return
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))
            return

        # Validate loaded data
        if not isinstance(data, dict) or 'steps' not in data:
            QMessageBox.warning(self, "Invalid File", "File does not contain a valid scenario.")
            return

        self.repeat_spin.setValue(data.get('repeat', 1))
        # Clear table and populate
        self.table.setRowCount(0)
        for step in data['steps']:
            stype = step.get('type', 'normal')
            if stype not in STEP_TYPES:
                continue
            dur = str(step.get('duration', DEFAULT_DURATIONS.get(stype, 30)))
            notes = step.get('notes', '')
            self._insert_row(self.table.rowCount(), stype, dur, notes)
        self._update_summary()

    def get_steps_raw(self) -> list:
        """Return the raw step list (dicts with type, duration, notes)."""
        steps = []
        for row in range(self.table.rowCount()):
            step_type = self.table.item(row, 0).text()
            try:
                duration = float(self.table.item(row, 1).text())
            except (ValueError, AttributeError):
                duration = DEFAULT_DURATIONS.get(step_type, 30)
            notes = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
            steps.append({
                'type': step_type,
                'duration': duration,
                'notes': notes,
            })
        return steps

    def get_repeat(self) -> int:
        return self.repeat_spin.value()

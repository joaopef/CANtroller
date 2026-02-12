"""
Simulator - Fake BMS/MCU simulation for CANtroller
Generates synthetic trip profiles and replays them as CAN messages.
"""
import csv
import math
import os
import random
from dataclasses import dataclass, field
from typing import List, Optional, Callable

from PyQt6.QtCore import QObject, pyqtSignal, QTimer


# === CAN Signal Definitions ===
# Based on CAN Data Points.csv

# BMS frame: GET_SOC_1
BMS_CAN_ID = 0x18F81280
# Voltage: bits 0-15, factor 0.1 V
# Current: bits 16-31, factor 0.05 A
# SOC: bits 32-39, factor 1 %
# SOH: bits 40-47, factor 1 %
# Full Charge Cycles: bits 48-63, factor 1

# MCU frame: GET_MCU_KM
MCU_CAN_ID = 0x18F86890
# Speed: bits 0-7, factor 1 km/h
# Total mileage: bits 8-31, factor 1 km
# Current mileage: bits 32-39, factor 1 km
# Reserved: bits 40-44
# Gear: bits 45-47, factor 1
# Flags: bits 48-63 (motor fail, grip fail, brake fail, etc.)


@dataclass
class TripDataPoint:
    """Single data point in a trip profile"""
    time_s: float       # Seconds into trip
    voltage_V: float    # Battery voltage (60-84V for 72V NMC pack)
    current_A: float    # Current (positive = discharge, negative = regen)
    soc_pct: float      # State of charge 0-100%
    soh_pct: float      # State of health 0-100%
    fc_cycles: int      # Full charge cycles count
    speed_kmh: int      # Vehicle speed 0-100 km/h
    total_mileage_km: int   # Odometer reading
    current_mileage_km: float  # Trip distance (fractional km)
    gear: int           # Gear 0-5


@dataclass
class TripProfile:
    """Complete trip profile as a time-series of data points"""
    name: str
    description: str
    data_points: List[TripDataPoint] = field(default_factory=list)
    duration_min: float = 30.0

    @property
    def duration_s(self) -> float:
        if self.data_points:
            return self.data_points[-1].time_s
        return self.duration_min * 60.0

    @property
    def point_count(self) -> int:
        return len(self.data_points)


class TripProfileGenerator:
    """Generates synthetic trip profiles with realistic battery behavior"""

    # Battery parameters — CTS Battery Technology NMC pouch cells, 20S config
    # Real specs: 72V nominal, 73Ah, 5256Wh, cutoff 60V
    PACK_VOLTAGE_FULL = 84.0    # 20S * 4.2V per cell (fully charged)
    PACK_VOLTAGE_NOMINAL = 72.0  # 20S * 3.6V nominal
    PACK_VOLTAGE_EMPTY = 60.0   # Cutoff voltage per spec
    PACK_CAPACITY_AH = 73.0     # 73 Ah pack
    MAX_CONTINUOUS_A = 110.0    # Max continuous discharge current
    MAX_PEAK_A = 250.0          # Peak current (5 seconds)
    REGEN_EFFICIENCY = 0.7      # Regenerative braking efficiency

    @classmethod
    def _voltage_from_soc(cls, soc_pct: float) -> float:
        """
        NMC lithium-ion voltage curve (20S pack).
        SOC 100% -> ~84V, SOC 0% -> ~60V
        """
        s = max(0.0, min(1.0, soc_pct / 100.0))
        # NMC cell: steep at top, flat middle, steep drop at bottom
        v_norm = (
            0.05 * math.exp(-20 * (1 - s))   # High-SOC steep region
            + 0.90 * s                          # Linear mid region
            + 0.05 * (1 - math.exp(-20 * s))   # Low-SOC steep region
        )
        voltage = cls.PACK_VOLTAGE_EMPTY + v_norm * (cls.PACK_VOLTAGE_FULL - cls.PACK_VOLTAGE_EMPTY)
        return round(voltage, 1)

    @classmethod
    def generate_city_trip(cls, duration_min: float = 30, 
                           start_soc: float = 85.0,
                           soh: float = 95.0,
                           fc_cycles: int = 120,
                           start_odometer: int = 1250) -> TripProfile:
        """
        City trip: stop-and-go traffic with variable speed.
        Includes regenerative braking when decelerating.
        """
        profile = TripProfile(
            name="City Trip",
            description=f"Stop-and-go city driving, {duration_min} min",
            duration_min=duration_min
        )

        total_seconds = int(duration_min * 60)
        step_s = 1.0
        soc = start_soc
        speed = 0.0
        prev_speed = 0.0
        trip_km = 0.0
        target_speed = 0
        next_event = 0

        for t in range(0, total_seconds + 1, int(step_s)):
            if t >= next_event:
                r = random.random()
                if r < 0.15:
                    target_speed = 0
                    next_event = t + random.randint(8, 25)
                elif r < 0.40:
                    target_speed = random.randint(15, 30)
                    next_event = t + random.randint(15, 40)
                elif r < 0.75:
                    target_speed = random.randint(30, 50)
                    next_event = t + random.randint(20, 50)
                else:
                    target_speed = random.randint(45, 60)
                    next_event = t + random.randint(10, 30)

            prev_speed = speed
            if speed < target_speed:
                speed = min(speed + random.uniform(1.5, 3.5), target_speed)
            elif speed > target_speed:
                speed = max(speed - random.uniform(2.0, 5.0), target_speed)

            speed_int = max(0, int(round(speed)))
            decel = prev_speed - speed  # Positive when decelerating

            # Current: discharge when driving, regen when braking
            if speed_int == 0 and decel <= 0:
                current = random.uniform(0.5, 2.0)  # Idle draw
            elif decel > 1.0 and speed_int > 5:
                # Regenerative braking — negative current
                regen_current = decel * 3.0 * cls.REGEN_EFFICIENCY + random.uniform(-2, 2)
                current = -max(1.0, min(regen_current, 30.0))
            else:
                base_current = speed_int * 0.8 + random.uniform(-3, 5)
                current = max(1.0, min(base_current, cls.MAX_CONTINUOUS_A))

            # SOC change (positive current = discharge, negative = charge/regen)
            energy_wh = current * cls.PACK_VOLTAGE_NOMINAL * (step_s / 3600.0)
            total_energy_wh = cls.PACK_CAPACITY_AH * cls.PACK_VOLTAGE_NOMINAL
            soc -= (energy_wh / total_energy_wh) * 100.0
            soc = max(0, min(100, soc))

            trip_km += speed_int * (step_s / 3600.0)

            if speed_int == 0:
                gear = 0
            elif speed_int < 15:
                gear = 1
            elif speed_int < 30:
                gear = 2
            elif speed_int < 45:
                gear = 3
            else:
                gear = 4

            voltage = cls._voltage_from_soc(soc)

            profile.data_points.append(TripDataPoint(
                time_s=float(t),
                voltage_V=voltage,
                current_A=round(current, 2),
                soc_pct=round(soc, 1),
                soh_pct=soh,
                fc_cycles=fc_cycles,
                speed_kmh=speed_int,
                total_mileage_km=start_odometer + int(trip_km),
                current_mileage_km=round(trip_km, 1),
                gear=gear
            ))

            if soc <= 0:
                break

        return profile

    @classmethod
    def generate_highway_trip(cls, duration_min: float = 60,
                               start_soc: float = 95.0,
                               soh: float = 92.0,
                               fc_cycles: int = 200,
                               start_odometer: int = 5200) -> TripProfile:
        """
        Highway trip: steady high speed with minor variations.
        Includes occasional regen during speed adjustments.
        """
        profile = TripProfile(
            name="Highway Trip",
            description=f"Highway cruising, {duration_min} min",
            duration_min=duration_min
        )

        total_seconds = int(duration_min * 60)
        step_s = 1.0
        soc = start_soc
        speed = 0.0
        prev_speed = 0.0
        trip_km = 0.0
        cruise_speed = random.randint(55, 70)
        accel_time = 30

        for t in range(0, total_seconds + 1, int(step_s)):
            prev_speed = speed
            if t < accel_time:
                speed = cruise_speed * (t / accel_time)
            else:
                speed = cruise_speed + random.uniform(-3, 3)

            speed_int = max(0, int(round(speed)))
            decel = prev_speed - speed

            # Current with regen during deceleration
            if decel > 1.0 and speed_int > 10:
                regen_current = decel * 4.0 * cls.REGEN_EFFICIENCY + random.uniform(-1, 2)
                current = -max(1.0, min(regen_current, 40.0))
            elif t < accel_time:
                current = speed_int * 1.2 + random.uniform(0, 8)
            else:
                current = cruise_speed * 0.7 + random.uniform(-2, 4)
            current = max(-40.0, min(current, cls.MAX_CONTINUOUS_A))

            energy_wh = current * cls.PACK_VOLTAGE_NOMINAL * (step_s / 3600.0)
            total_energy_wh = cls.PACK_CAPACITY_AH * cls.PACK_VOLTAGE_NOMINAL
            soc -= (energy_wh / total_energy_wh) * 100.0
            soc = max(0, min(100, soc))

            trip_km += speed_int * (step_s / 3600.0)

            if speed_int < 15:
                gear = 1
            elif speed_int < 30:
                gear = 2
            elif speed_int < 45:
                gear = 3
            elif speed_int < 60:
                gear = 4
            else:
                gear = 5

            voltage = cls._voltage_from_soc(soc)

            profile.data_points.append(TripDataPoint(
                time_s=float(t),
                voltage_V=voltage,
                current_A=round(current, 2),
                soc_pct=round(soc, 1),
                soh_pct=soh,
                fc_cycles=fc_cycles,
                speed_kmh=speed_int,
                total_mileage_km=start_odometer + int(trip_km),
                current_mileage_km=round(trip_km, 1),
                gear=gear
            ))

            if soc <= 0:
                break

        return profile

    @classmethod
    def generate_charge_cycle(cls, duration_min: float = 120,
                                start_soc: float = 10.0,
                                soh: float = 90.0,
                                fc_cycles: int = 300,
                                start_odometer: int = 8500) -> TripProfile:
        """
        Charge cycle: battery charging from low SOC.
        CC-CV profile: constant current until ~80%, then tapering current.
        Speed is 0 (vehicle stationary).
        """
        profile = TripProfile(
            name="Charge Cycle",
            description=f"Battery charging, {duration_min} min",
            duration_min=duration_min
        )

        total_seconds = int(duration_min * 60)
        step_s = 1.0
        soc = start_soc
        target_soc = 100.0
        charge_current_max = 20.0  # 20A charge rate (0.5C for 40Ah)

        for t in range(0, total_seconds + 1, int(step_s)):
            # CC phase (constant current until ~80%)
            if soc < 80:
                current = -charge_current_max  # Negative = charging
            else:
                # CV phase: taper current as approaching full
                taper = max(0.1, 1.0 - ((soc - 80) / 20.0))
                current = -charge_current_max * taper

            # SOC increase (charging)
            energy_wh = abs(current) * cls.PACK_VOLTAGE_NOMINAL * (step_s / 3600.0)
            total_energy_wh = cls.PACK_CAPACITY_AH * cls.PACK_VOLTAGE_NOMINAL
            soc += (energy_wh / total_energy_wh) * 100.0
            soc = min(target_soc, soc)

            voltage = cls._voltage_from_soc(soc)

            profile.data_points.append(TripDataPoint(
                time_s=float(t),
                voltage_V=voltage,
                current_A=round(abs(current), 2),  # Display as positive
                soc_pct=round(soc, 1),
                soh_pct=soh,
                fc_cycles=fc_cycles,
                speed_kmh=0,
                total_mileage_km=start_odometer,
                current_mileage_km=0.0,
                gear=0
            ))

            if soc >= target_soc:
                break

        return profile

    @classmethod
    def get_available_profiles(cls) -> List[dict]:
        """List of available profile generators with metadata"""
        return [
            {
                "name": "City Trip (30 min)",
                "generator": cls.generate_city_trip,
                "kwargs": {"duration_min": 30},
                "description": "Stop-and-go city driving with variable speed"
            },
            {
                "name": "Highway Trip (60 min)",
                "generator": cls.generate_highway_trip,
                "kwargs": {"duration_min": 60},
                "description": "Steady highway cruising at 55-70 km/h"
            },
            {
                "name": "Charge Cycle (120 min)",
                "generator": cls.generate_charge_cycle,
                "kwargs": {"duration_min": 120},
                "description": "CC-CV battery charging from low SOC"
            },
            {
                "name": "Short City Trip (10 min)",
                "generator": cls.generate_city_trip,
                "kwargs": {"duration_min": 10},
                "description": "Quick city run, 10 minutes"
            },
            {
                "name": "Long Highway Trip (120 min)",
                "generator": cls.generate_highway_trip,
                "kwargs": {"duration_min": 120},
                "description": "Extended highway drive, 2 hours"
            },
        ]

    @classmethod
    def load_csv_profile(cls, filepath: str) -> TripProfile:
        """
        Load a trip profile from a CSV file (real driving data).
        
        Supports the format from 'Data reading Teste conducao.csv':
        Columns auto-detected by header keywords:
          - Time: 'Tempo' or 'Time'
          - Voltage: 'DC Current (V)' or contains '(V)'
          - Current: 'DC Current (A)' or contains '(A)'
          - Speed: 'Velocidade' or 'Speed' (km/h, not rpm)
          - Total km: 'km total'
          - Current km: 'km atual'
          - Driving Mode: 'Driving Mode' or 'Mode'
        """
        filename = os.path.basename(filepath)
        profile = TripProfile(
            name=f"CSV: {filename}",
            description=f"Real trip data from {filename}"
        )

        with open(filepath, 'r', encoding='utf-8-sig') as f:
            lines = f.readlines()

        if len(lines) < 3:
            raise ValueError("CSV file too short — need header + data rows")

        # Parse header (first line) to find column indices
        header = lines[0].strip().split(',')
        col_map = {
            'time': -1, 'voltage': -1, 'current': -1,
            'speed_kmh': -1, 'km_total': -1, 'km_current': -1,
            'mode': -1
        }

        for i, col in enumerate(header):
            col_lower = col.strip().lower()
            if 'tempo' in col_lower or (col_lower == 'time' and 's' in col_lower):
                col_map['time'] = i
            elif '(v)' in col_lower and col_map['voltage'] == -1:
                col_map['voltage'] = i
            elif '(a)' in col_lower and col_map['current'] == -1:
                col_map['current'] = i
            elif ('velocidade' in col_lower or 'speed' in col_lower) and 'rpm' not in col_lower and col_map['speed_kmh'] == -1:
                col_map['speed_kmh'] = i
            elif 'km total' in col_lower:
                col_map['km_total'] = i
            elif 'km atu' in col_lower:
                col_map['km_current'] = i
            elif 'driving mode' in col_lower or 'mode' in col_lower:
                col_map['mode'] = i

        # Fallback: if 'time' not found, try first column 
        if col_map['time'] == -1:
            col_map['time'] = 0

        if col_map['voltage'] == -1:
            raise ValueError("Could not find voltage column in CSV header")

        def safe_float(val: str, default: float = 0.0) -> float:
            """Safely parse a float, handling #N/A and empty strings"""
            val = val.strip()
            if not val or val.startswith('#') or val == '---':
                return default
            try:
                return float(val)
            except ValueError:
                return default

        def safe_int(val: str, default: int = 0) -> int:
            return int(safe_float(val, float(default)))

        def mode_to_gear(mode_str: str) -> int:
            """Map driving mode string to gear value"""
            mode = mode_str.strip().lower()
            if mode in ('park', 'p', ''):
                return 0
            elif mode in ('eco', 'e'):
                return 1
            elif mode in ('normal', 'n', 'd'):
                return 2
            elif mode in ('sport', 's'):
                return 3
            else:
                return 2  # Default to normal

        # Estimate SOC from voltage using the pack voltage range
        # User's pack: ~75V full, ~60V empty (higher than our default constants)
        # We'll detect the actual range from the data
        voltages = []
        for line in lines[2:]:
            parts = line.strip().split(',')
            if len(parts) > col_map['voltage'] and parts[col_map['time']].strip():
                v = safe_float(parts[col_map['voltage']])
                if v > 0:
                    voltages.append(v)

        if not voltages:
            raise ValueError("No valid voltage data found in CSV")

        v_max = max(voltages)
        v_min = min(voltages)
        # Add small margin for SOC estimation
        v_range_full = v_max + 1.0  # Slightly above observed max
        v_range_empty = max(v_min - 5.0, v_max * 0.65)  # ~65% of max as empty

        # Parse data rows (skip header rows)
        for line in lines[2:]:
            parts = line.strip().split(',')
            time_str = parts[col_map['time']].strip() if col_map['time'] < len(parts) else ''
            if not time_str or time_str.startswith('#'):
                continue

            time_s = safe_float(time_str)
            if time_s <= 0 and len(profile.data_points) > 0:
                continue

            voltage = safe_float(parts[col_map['voltage']] if col_map['voltage'] < len(parts) else '0')
            current = abs(safe_float(parts[col_map['current']] if col_map['current'] < len(parts) else '0'))
            speed = safe_int(parts[col_map['speed_kmh']] if col_map['speed_kmh'] >= 0 and col_map['speed_kmh'] < len(parts) else '0')
            km_total = safe_int(parts[col_map['km_total']] if col_map['km_total'] >= 0 and col_map['km_total'] < len(parts) else '0')
            km_current_raw = safe_float(parts[col_map['km_current']] if col_map['km_current'] >= 0 and col_map['km_current'] < len(parts) else '0')
            km_current = int(km_current_raw)
            mode_str = parts[col_map['mode']].strip() if col_map['mode'] >= 0 and col_map['mode'] < len(parts) else ''

            # Estimate SOC from voltage (linear mapping within observed range)
            if v_range_full > v_range_empty:
                soc = ((voltage - v_range_empty) / (v_range_full - v_range_empty)) * 100.0
                soc = max(0, min(100, soc))
            else:
                soc = 50.0

            gear = mode_to_gear(mode_str)

            profile.data_points.append(TripDataPoint(
                time_s=time_s,
                voltage_V=round(voltage, 1),
                current_A=round(current, 2),
                soc_pct=round(soc, 1),
                soh_pct=95.0,  # Unknown from CSV, assume good
                fc_cycles=0,   # Unknown from CSV
                speed_kmh=max(0, speed),
                total_mileage_km=km_total,
                current_mileage_km=km_current,
                gear=gear
            ))

        if not profile.data_points:
            raise ValueError("No valid data points found in CSV")

        profile.duration_min = profile.data_points[-1].time_s / 60.0
        return profile



# === CAN Frame Encoding ===

def encode_bms_frame(dp: TripDataPoint) -> List[int]:
    """
    Encode BMS data point into 8 CAN data bytes for GET_SOC_1 (0x18F81280).

    Layout (BIG-ENDIAN to match the decoder):
      Byte 0-1: Voltage (16-bit, factor 0.1V, big-endian)
      Byte 2-3: Current (16-bit, factor 0.05A, big-endian)
      Byte 4:   SOC (8-bit, factor 1%)
      Byte 5:   SOH (8-bit, factor 1%)
      Byte 6-7: Full Charge Cycles (16-bit, factor 1, big-endian)
    """
    # Voltage: value / factor = raw
    v_raw = int(round(dp.voltage_V / 0.1))
    v_raw = max(0, min(v_raw, 0xFFFF))

    # Current: use absolute value for encoding (factor 0.05)
    c_raw = int(round(abs(dp.current_A) / 0.05))
    c_raw = max(0, min(c_raw, 0xFFFF))

    soc = max(0, min(int(round(dp.soc_pct)), 255))
    soh = max(0, min(int(round(dp.soh_pct)), 255))

    fc = max(0, min(dp.fc_cycles, 0xFFFF))

    data = [
        (v_raw >> 8) & 0xFF, v_raw & 0xFF,         # Voltage BE
        (c_raw >> 8) & 0xFF, c_raw & 0xFF,         # Current BE
        soc,                                         # SOC
        soh,                                         # SOH
        (fc >> 8) & 0xFF, fc & 0xFF,               # FC Cycles BE
    ]
    return data


def encode_mcu_frame(dp: TripDataPoint) -> List[int]:
    """
    Encode MCU data point into 8 CAN data bytes for GET_MCU_KM (0x18F86890).

    Layout (BIG-ENDIAN to match the decoder):
      Byte 0:   Speed (8-bit, factor 1 km/h)
      Byte 1-3: Total mileage (24-bit, factor 1 km, big-endian)
      Byte 4:   Current mileage (8-bit, factor 1 km)
      Byte 5:   bits 0-4 reserved, bits 5-7 = gear (3-bit)
      Byte 6:   Flags (motor fail, grip fail, brake fail, etc.) — all 0
      Byte 7:   Reserved — 0
    """
    speed = max(0, min(dp.speed_kmh, 255))

    total_km = max(0, min(dp.total_mileage_km, 0xFFFFFF))
    current_km = max(0, min(int(round(dp.current_mileage_km)), 255))
    gear = max(0, min(dp.gear, 7))

    data = [
        speed,                                                         # Speed
        (total_km >> 16) & 0xFF, (total_km >> 8) & 0xFF, total_km & 0xFF,  # Total mileage BE
        current_km,                                                    # Current mileage
        (gear & 0x07) << 5,                                           # Gear in bits 5-7
        0x00,                                                          # Flags (no faults)
        0x00,                                                          # Reserved
    ]
    return data


class SimulationEngine(QObject):
    """
    Runs a trip simulation, sending CAN frames at a configurable rate.
    Completely independent from the existing TransmitMessage system.
    """

    # Signals
    progress_changed = pyqtSignal(int)          # Progress 0-100%
    data_updated = pyqtSignal(dict)             # Current data point values
    simulation_finished = pyqtSignal()
    simulation_started = pyqtSignal()
    simulation_paused = pyqtSignal(bool)        # True = paused, False = resumed
    status_message = pyqtSignal(str)            # Status text for status bar

    def __init__(self, can_manager):
        super().__init__()
        self._can_manager = can_manager
        self._profile: Optional[TripProfile] = None
        self._timer: Optional[QTimer] = None
        self._current_index: int = 0
        self._is_running: bool = False
        self._is_paused: bool = False
        self._playback_speed: float = 1.0
        self._send_interval_ms: int = 250  # Base interval between sends

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def playback_speed(self) -> float:
        return self._playback_speed

    @playback_speed.setter
    def playback_speed(self, value: float):
        self._playback_speed = max(0.5, min(value, 50.0))
        # Update timer interval if running
        if self._timer and self._is_running and not self._is_paused:
            self._timer.setInterval(self._effective_interval_ms)

    @property
    def _effective_interval_ms(self) -> int:
        """Timer interval adjusted for playback speed"""
        return max(10, int(self._send_interval_ms / self._playback_speed))

    @property
    def current_data(self) -> Optional[TripDataPoint]:
        if self._profile and 0 <= self._current_index < len(self._profile.data_points):
            return self._profile.data_points[self._current_index]
        return None

    def load_profile(self, profile: TripProfile):
        """Load a trip profile for simulation"""
        self.stop()
        self._profile = profile
        self._current_index = 0

    def start(self) -> bool:
        """Start or resume the simulation"""
        if not self._profile or not self._profile.data_points:
            self.status_message.emit("No profile loaded")
            return False

        if not self._can_manager.is_connected:
            self.status_message.emit("CAN bus not connected - cannot start simulation")
            return False

        if self._is_paused:
            # Resume
            self._is_paused = False
            self._timer.start(self._effective_interval_ms)
            self.simulation_paused.emit(False)
            self.status_message.emit(f"▶ Simulation resumed: {self._profile.name}")
            return True

        # Fresh start
        self._current_index = 0
        self._is_running = True
        self._is_paused = False

        # Calculate steps to skip per tick based on profile resolution vs send interval
        # Profile has 1Hz data, we send at send_interval rate
        # At 250ms interval and 1x speed, we advance ~0.25s per tick
        # We'll just advance by time matching

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._effective_interval_ms)

        self.simulation_started.emit()
        self.status_message.emit(f"▶ Simulation started: {self._profile.name}")
        return True

    def pause(self):
        """Pause the simulation"""
        if self._is_running and not self._is_paused:
            self._is_paused = True
            if self._timer:
                self._timer.stop()
            self.simulation_paused.emit(True)
            self.status_message.emit(f"⏸ Simulation paused: {self._profile.name}")

    def stop(self):
        """Stop the simulation"""
        self._is_running = False
        self._is_paused = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._current_index = 0
        self.status_message.emit("Simulation stopped")

    def _tick(self):
        """Called by timer: send current data point and advance"""
        if not self._profile or not self._is_running:
            return

        if self._current_index >= len(self._profile.data_points):
            # Simulation complete
            self._is_running = False
            if self._timer:
                self._timer.stop()
            self.progress_changed.emit(100)
            self.simulation_finished.emit()
            self.status_message.emit(f"✓ Simulation complete: {self._profile.name}")
            return

        dp = self._profile.data_points[self._current_index]

        # Encode and send BMS frame
        bms_data = encode_bms_frame(dp)
        self._can_manager.send_message(BMS_CAN_ID, bms_data, is_extended=True)

        # Encode and send MCU frame
        mcu_data = encode_mcu_frame(dp)
        self._can_manager.send_message(MCU_CAN_ID, mcu_data, is_extended=True)

        # Calculate progress
        progress = int((self._current_index / len(self._profile.data_points)) * 100)
        self.progress_changed.emit(progress)

        # Emit current data for UI display
        self.data_updated.emit({
            'time_s': dp.time_s,
            'voltage': dp.voltage_V,
            'current': dp.current_A,
            'soc': dp.soc_pct,
            'soh': dp.soh_pct,
            'speed': dp.speed_kmh,
            'mileage': dp.current_mileage_km,
            'gear': dp.gear,
        })

        # Update status bar
        self.status_message.emit(
            f"🔄 {self._profile.name} — "
            f"SOC: {dp.soc_pct:.0f}% | "
            f"{dp.voltage_V:.1f}V | "
            f"{dp.current_A:.1f}A | "
            f"{dp.speed_kmh} km/h | "
            f"x{self._playback_speed:.0f}"
        )

        # Advance index — skip data points based on playback speed
        # At 1x: 250ms interval, profile at 1Hz → advance ~0.25 points per tick
        # We accumulate and step when >= 1
        steps = max(1, int(self._playback_speed * self._send_interval_ms / 1000.0))
        self._current_index += steps

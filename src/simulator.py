"""
Simulator - Fake BMS/MCU simulation for CANtroller
Generates synthetic trip profiles and replays them as CAN messages.
"""
import csv
import logging
import math
import os
import random
from dataclasses import dataclass, field
from typing import List, Optional, Callable

log = logging.getLogger('cantroller.simulator')

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# Import PyBaMM battery model (optional dependency)
try:
    from battery_model import PyBaMMBatteryModel, PYBAMM_AVAILABLE
except ImportError:
    PYBAMM_AVAILABLE = False


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

# BMS Temperature frame: battery dynamic parameters 3 — Temperature detection point
# Real BMS1 protocol row 16 (0x18F82881), source address 0x80 for simulator
BMS_TEMP_CAN_ID = 0x18F82880
# Byte 0-1: Cell Temp Average (16-bit, factor 0.1°C, big-endian)
# Byte 2-3: Cell Temp Max (16-bit, factor 0.1°C, big-endian)
# Byte 4-5: Cell Temp Min (16-bit, factor 0.1°C, big-endian)
# Byte 6-7: Reserved


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
    temperature_C: float = 25.0  # Cell temperature in °C


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
    # Real specs: 72V nominal, 40Ah (per BMS datasheet), cutoff 60V
    PACK_VOLTAGE_FULL = 84.0    # 20S * 4.2V per cell (fully charged)
    PACK_VOLTAGE_NOMINAL = 72.0  # 20S * 3.6V nominal
    PACK_VOLTAGE_EMPTY = 60.0   # Cutoff voltage per spec
    PACK_CAPACITY_AH = 40.0     # 40 Ah pack (per BMS datasheet)
    MAX_CONTINUOUS_A = 110.0    # Max continuous discharge current
    MAX_PEAK_A = 250.0          # Peak current (5 seconds)
    AMBIENT_TEMP_C = 25.0       # Default ambient temperature
    THERMAL_RESISTANCE = 0.004  # °C per Amp (simple thermal model, tuned to real data)

    # Driving modes — based on real Fulgora vehicle specs
    # Mode: {gear, top_speed_kmh, power_pct (% of MAX_CONTINUOUS_A), regen_pct (brake regen %)}
    DRIVING_MODES = {
        0: {'name': 'Park',    'gear': 0, 'top_speed': 0,   'power_pct': 0.0,  'regen_pct': 0.0},
        1: {'name': 'Eco',     'gear': 1, 'top_speed': 45,  'power_pct': 0.30, 'regen_pct': 0.20},
        2: {'name': 'Normal',  'gear': 2, 'top_speed': 75,  'power_pct': 0.50, 'regen_pct': 0.20},
        3: {'name': 'Sport',   'gear': 3, 'top_speed': 100, 'power_pct': 1.00, 'regen_pct': 0.0},
    }

    # === Vehicle Dynamics (Fulgora EV motorcycle) ===
    VEHICLE_MASS_KG = 180.0         # Motorcycle curb weight
    RIDER_MASS_KG = 75.0            # Average rider
    TOTAL_MASS_KG = 255.0           # Vehicle + rider
    CRR = 0.015                     # Rolling resistance (motorcycle tire on asphalt)
    CDA = 0.4                       # Drag area CdA in m² (rider + fairing)
    AIR_DENSITY = 1.225             # kg/m³ at sea level
    GRAVITY = 9.81                  # m/s²
    MOTOR_EFFICIENCY = 0.85         # Motor efficiency
    DRIVETRAIN_EFFICIENCY = 0.95    # Single-speed reduction
    REGEN_EFFICIENCY = 0.60         # Regenerative braking recovery
    IDLE_DRAW_A = 1.0               # Electronics idle current

    # === WMTC Speed Traces ===
    # UN ECE GTR No. 2, Class 1-2 (v_max ≤ 100 km/h)
    # Breakpoints: (time_s, speed_kmh) — linear interpolation between points
    # Part 1: Urban cycle, 600s, max ~50 km/h, avg ~25 km/h
    _WMTC_PART1_BREAKPOINTS = [
        # Idle
        (0, 0), (20, 0),
        # Micro-trip 1: gentle urban acceleration
        (21, 0), (35, 25), (50, 30), (60, 28), (72, 0),
        (85, 0),
        # Micro-trip 2: moderate urban
        (86, 0), (100, 32), (110, 36), (125, 34), (135, 38),
        (148, 30), (158, 0), (170, 0),
        # Micro-trip 3: higher speed urban (peak ~50 km/h)
        (171, 0), (185, 35), (200, 48), (220, 50), (240, 46),
        (255, 50), (270, 40), (282, 0), (295, 0),
        # Micro-trip 4: varied urban with undulations
        (296, 0), (310, 30), (325, 42), (340, 45), (355, 40),
        (370, 48), (385, 35), (395, 0), (410, 0),
        # Micro-trip 5: moderate cruise
        (411, 0), (425, 38), (440, 45), (460, 50), (480, 48),
        (495, 42), (505, 0), (520, 0),
        # Micro-trip 6: final urban segment
        (521, 0), (535, 30), (548, 40), (558, 38), (568, 42),
        (578, 30), (590, 15), (598, 0), (600, 0),
    ]

    # Part 2: Extra-urban/rural cycle, 600s, max ~95 km/h
    # Used when generating the extended WMTC Part 1+2 profile
    _WMTC_PART2_BREAKPOINTS = [
        # Start from stop
        (0, 0), (5, 0),
        # Suburban acceleration
        (6, 0), (25, 55), (50, 60), (75, 65), (100, 60),
        # Brief deceleration zone
        (115, 50), (125, 55),
        # Highway entry ramp
        (140, 70), (165, 80), (185, 85), (210, 90), (235, 95),
        # High-speed cruise with variation
        (260, 90), (280, 95), (300, 92),
        # Slow section (construction/traffic)
        (320, 80), (340, 70), (355, 55), (370, 60),
        # Return to high speed
        (390, 75), (415, 90), (440, 95), (465, 88),
        # Deceleration to suburban
        (485, 70), (500, 55), (520, 45),
        # Low speed finish
        (540, 30), (555, 20), (568, 0), (580, 0),
        # Short final burst
        (581, 0), (590, 25), (598, 15), (600, 0),
    ]

    @staticmethod
    def _speed_to_gear(speed_kmh: float) -> int:
        """Dynamic gear (driving mode) selection based on current speed."""
        if speed_kmh < 1:
            return 0   # Park / Neutral
        elif speed_kmh <= 45:
            return 1   # Eco
        elif speed_kmh <= 75:
            return 2   # Normal
        else:
            return 3   # Sport

    @classmethod
    def _interpolate_speed_trace(cls, breakpoints: list, time_s: float) -> float:
        """Linearly interpolate speed from a breakpoint list at a given time."""
        if time_s <= breakpoints[0][0]:
            return breakpoints[0][1]
        if time_s >= breakpoints[-1][0]:
            return breakpoints[-1][1]
        for i in range(len(breakpoints) - 1):
            t0, v0 = breakpoints[i]
            t1, v1 = breakpoints[i + 1]
            if t0 <= time_s <= t1:
                if t1 == t0:
                    return v0
                frac = (time_s - t0) / (t1 - t0)
                return v0 + frac * (v1 - v0)
        return 0.0

    @classmethod
    def _speed_to_current(cls, speed_kmh: float, prev_speed_kmh: float,
                          dt_s: float, voltage_V: float) -> float:
        """
        Vehicle dynamics model: convert speed + acceleration into battery current.

        Uses rolling resistance, aerodynamic drag, and inertial force to compute
        mechanical power, then converts to electrical current via motor/drivetrain
        efficiency. Accounts for regenerative braking during deceleration.
        """
        v = speed_kmh / 3.6      # m/s
        v_prev = prev_speed_kmh / 3.6
        a = (v - v_prev) / dt_s if dt_s > 0 else 0.0

        # Force components
        F_inertia = cls.TOTAL_MASS_KG * a
        F_rolling = cls.CRR * cls.TOTAL_MASS_KG * cls.GRAVITY if v > 0.1 else 0.0
        F_aero = 0.5 * cls.AIR_DENSITY * cls.CDA * v * v
        F_total = F_inertia + F_rolling + F_aero

        P_mech = F_total * v  # Mechanical power in Watts

        if P_mech >= 0:
            # Driving: discharge
            eta = cls.MOTOR_EFFICIENCY * cls.DRIVETRAIN_EFFICIENCY
            P_elec = P_mech / eta if eta > 0 else 0.0
            current = P_elec / voltage_V if voltage_V > 0 else 0.0
        else:
            # Braking: regenerative charge (negative current)
            eta = cls.MOTOR_EFFICIENCY * cls.DRIVETRAIN_EFFICIENCY * cls.REGEN_EFFICIENCY
            P_regen = P_mech * eta  # Negative watts recovered
            current = P_regen / voltage_V if voltage_V > 0 else 0.0

        # Idle electronics draw when stationary
        if speed_kmh < 1 and current < cls.IDLE_DRAW_A:
            current = cls.IDLE_DRAW_A

        # Clamp to pack limits
        current = max(-cls.MAX_CONTINUOUS_A * 0.3, min(current, cls.MAX_CONTINUOUS_A))
        return current

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
                           start_odometer: int = 1250,
                           driving_mode: int = 2) -> TripProfile:
        """
        City trip: stop-and-go traffic with variable speed.
        Uses driving mode parameters for speed/power/regen limits.
        Default: Mode 2 (Normal) — 75 km/h, 50% power, 20% regen.
        """
        mode = cls.DRIVING_MODES.get(driving_mode, cls.DRIVING_MODES[2])
        max_speed = mode['top_speed']
        max_current = cls.MAX_CONTINUOUS_A * mode['power_pct']
        regen_efficiency = mode['regen_pct']
        gear = mode['gear']

        profile = TripProfile(
            name=f"City Trip ({mode['name']})",
            description=f"Stop-and-go city driving, {duration_min} min, Mode {driving_mode} ({mode['name']})",
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
                    target_speed = random.randint(15, min(30, max_speed))
                    next_event = t + random.randint(15, 40)
                elif r < 0.75:
                    target_speed = random.randint(30, min(50, max_speed))
                    next_event = t + random.randint(20, 50)
                else:
                    target_speed = random.randint(min(45, max_speed), min(60, max_speed))
                    next_event = t + random.randint(10, 30)

            prev_speed = speed
            if speed < target_speed:
                speed = min(speed + random.uniform(1.5, 3.5), target_speed)
            elif speed > target_speed:
                speed = max(speed - random.uniform(2.0, 5.0), target_speed)

            # Enforce mode speed limit
            speed = min(speed, max_speed)
            speed_int = max(0, int(round(speed)))
            decel = prev_speed - speed  # Positive when decelerating

            # Current: discharge when driving, regen when braking
            if speed_int == 0 and decel <= 0:
                current = random.uniform(0.5, 2.0)  # Idle draw
            elif decel > 1.0 and speed_int > 5 and regen_efficiency > 0:
                # Regenerative braking — negative current (limited by mode regen %)
                regen_current = decel * 3.0 * regen_efficiency + random.uniform(-1, 1)
                current = -max(0.5, min(regen_current, max_current * 0.3))
            else:
                base_current = speed_int * 0.8 + random.uniform(-3, 5)
                current = max(1.0, min(base_current, max_current))

            # SOC change (positive current = discharge, negative = charge/regen)
            energy_wh = current * cls.PACK_VOLTAGE_NOMINAL * (step_s / 3600.0)
            total_energy_wh = cls.PACK_CAPACITY_AH * cls.PACK_VOLTAGE_NOMINAL
            soc -= (energy_wh / total_energy_wh) * 100.0
            soc = max(0, min(100, soc))

            trip_km += speed_int * (step_s / 3600.0)

            voltage = cls._voltage_from_soc(soc)

            # Simple fallback temperature model: ambient + thermal rise from current
            if t == 0:
                temp_C = cls.AMBIENT_TEMP_C
            else:
                # Thermal inertia: slow rise proportional to |current|
                prev_temp = profile.data_points[-1].temperature_C if profile.data_points else cls.AMBIENT_TEMP_C
                heat_gain = cls.THERMAL_RESISTANCE * abs(current) * step_s
                heat_loss = 0.002 * (prev_temp - cls.AMBIENT_TEMP_C) * step_s
                temp_C = prev_temp + heat_gain - heat_loss

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
                gear=gear,
                temperature_C=round(temp_C, 1)
            ))

            if soc <= 0:
                break

        # If PyBaMM is available, replace voltage/SOC/temperature with physics model
        cls._apply_pybamm_if_available(profile, start_soc / 100.0)

        return profile

    @classmethod
    def generate_highway_trip(cls, duration_min: float = 60,
                               start_soc: float = 95.0,
                               soh: float = 92.0,
                               fc_cycles: int = 200,
                               start_odometer: int = 5200,
                               driving_mode: int = 3) -> TripProfile:
        """
        Highway trip: steady high speed with minor variations.
        Default: Mode 3 (Sport) — no speed limit, 100% power, no regen.
        """
        mode = cls.DRIVING_MODES.get(driving_mode, cls.DRIVING_MODES[3])
        max_speed = mode['top_speed']
        max_current = cls.MAX_CONTINUOUS_A * mode['power_pct']
        regen_efficiency = mode['regen_pct']
        gear = mode['gear']

        profile = TripProfile(
            name=f"Highway Trip ({mode['name']})",
            description=f"Highway cruising, {duration_min} min, Mode {driving_mode} ({mode['name']})",
            duration_min=duration_min
        )

        total_seconds = int(duration_min * 60)
        step_s = 1.0
        soc = start_soc
        speed = 0.0
        prev_speed = 0.0
        trip_km = 0.0
        cruise_speed = random.randint(55, min(70, max_speed))
        accel_time = 30

        for t in range(0, total_seconds + 1, int(step_s)):
            prev_speed = speed
            if t < accel_time:
                speed = cruise_speed * (t / accel_time)
            else:
                speed = cruise_speed + random.uniform(-3, 3)

            # Enforce mode speed limit
            speed = min(speed, max_speed)
            speed_int = max(0, int(round(speed)))
            decel = prev_speed - speed

            # Current with regen during deceleration (only if mode allows it)
            if decel > 1.0 and speed_int > 10 and regen_efficiency > 0:
                regen_current = decel * 4.0 * regen_efficiency + random.uniform(-1, 1)
                current = -max(0.5, min(regen_current, max_current * 0.3))
            elif t < accel_time:
                current = speed_int * 1.2 + random.uniform(0, 8)
            else:
                current = cruise_speed * 0.7 + random.uniform(-2, 4)
            current = max(-max_current * 0.3, min(current, max_current))

            energy_wh = current * cls.PACK_VOLTAGE_NOMINAL * (step_s / 3600.0)
            total_energy_wh = cls.PACK_CAPACITY_AH * cls.PACK_VOLTAGE_NOMINAL
            soc -= (energy_wh / total_energy_wh) * 100.0
            soc = max(0, min(100, soc))

            trip_km += speed_int * (step_s / 3600.0)

            voltage = cls._voltage_from_soc(soc)

            # Simple fallback temperature model
            if t == 0:
                temp_C = cls.AMBIENT_TEMP_C
            else:
                prev_temp = profile.data_points[-1].temperature_C if profile.data_points else cls.AMBIENT_TEMP_C
                heat_gain = cls.THERMAL_RESISTANCE * abs(current) * step_s
                heat_loss = 0.002 * (prev_temp - cls.AMBIENT_TEMP_C) * step_s
                temp_C = prev_temp + heat_gain - heat_loss

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
                gear=gear,
                temperature_C=round(temp_C, 1)
            ))

            if soc <= 0:
                break

        # If PyBaMM is available, replace voltage/SOC/temperature with physics model
        cls._apply_pybamm_if_available(profile, start_soc / 100.0)

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

            # Charging temperature model (lower currents, slower heating)
            if t == 0:
                temp_C = cls.AMBIENT_TEMP_C
            else:
                prev_temp = profile.data_points[-1].temperature_C if profile.data_points else cls.AMBIENT_TEMP_C
                heat_gain = cls.THERMAL_RESISTANCE * abs(current) * step_s * 0.5  # Less heat during charge
                heat_loss = 0.003 * (prev_temp - cls.AMBIENT_TEMP_C) * step_s
                temp_C = prev_temp + heat_gain - heat_loss

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
                gear=0,
                temperature_C=round(temp_C, 1)
            ))

            if soc >= target_soc:
                break

        # If PyBaMM is available, replace voltage/SOC/temperature with physics model
        cls._apply_pybamm_if_available(profile, start_soc / 100.0)

        return profile

    @classmethod
    def generate_wmtc_class1(cls, parts: list = None,
                              start_soc: float = 90.0,
                              soh: float = 95.0,
                              fc_cycles: int = 120,
                              start_odometer: int = 1250) -> TripProfile:
        """
        WMTC Class 1-2 standardized motorcycle driving cycle.
        UN ECE GTR No. 2 — for vehicles with v_max ≤ 100 km/h.

        Args:
            parts: Which parts to include. Default [1] for Part 1 only (urban, 600s).
                   Use [1, 2] for Part 1 + Part 2 (urban + extra-urban, 1200s).
            start_soc: Initial state of charge (%).
            soh: State of health (%).
            fc_cycles: Full charge cycle count.
            start_odometer: Odometer reading (km).

        The speed trace is deterministic (same every run), making this profile
        ideal for reproducible dataset collection and benchmarking.
        Gears are assigned dynamically based on speed (Eco/Normal/Sport).
        Current is computed from vehicle dynamics (mass, drag, rolling resistance).
        """
        if parts is None:
            parts = [1]

        # Build the combined speed trace breakpoints
        breakpoints = []
        if 1 in parts:
            breakpoints.extend(cls._WMTC_PART1_BREAKPOINTS)
        if 2 in parts:
            offset = breakpoints[-1][0] if breakpoints else 0.0
            for t, v in cls._WMTC_PART2_BREAKPOINTS:
                breakpoints.append((t + offset, v))

        total_seconds = int(breakpoints[-1][0])
        parts_str = '+'.join(str(p) for p in sorted(parts))
        profile = TripProfile(
            name=f"WMTC Class 1-2 Part {parts_str}",
            description=f"WMTC standardized motorcycle cycle, Part {parts_str}, "
                        f"{total_seconds}s, deterministic speed trace",
            duration_min=total_seconds / 60.0
        )

        step_s = 1.0
        soc = start_soc
        trip_km = 0.0
        prev_speed = 0.0

        for t in range(0, total_seconds + 1):
            speed = cls._interpolate_speed_trace(breakpoints, float(t))
            speed_int = max(0, int(round(speed)))
            gear = cls._speed_to_gear(speed)
            voltage = cls._voltage_from_soc(soc)

            # Vehicle dynamics current model
            current = cls._speed_to_current(speed, prev_speed, step_s, voltage)

            # SOC change
            energy_wh = current * cls.PACK_VOLTAGE_NOMINAL * (step_s / 3600.0)
            total_energy_wh = cls.PACK_CAPACITY_AH * cls.PACK_VOLTAGE_NOMINAL
            soc -= (energy_wh / total_energy_wh) * 100.0
            soc = max(0, min(100, soc))

            trip_km += speed_int * (step_s / 3600.0)
            voltage = cls._voltage_from_soc(soc)

            # Temperature model
            if t == 0:
                temp_C = cls.AMBIENT_TEMP_C
            else:
                prev_temp = profile.data_points[-1].temperature_C
                heat_gain = cls.THERMAL_RESISTANCE * abs(current) * step_s
                heat_loss = 0.002 * (prev_temp - cls.AMBIENT_TEMP_C) * step_s
                temp_C = prev_temp + heat_gain - heat_loss

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
                gear=gear,
                temperature_C=round(temp_C, 1)
            ))

            prev_speed = speed
            if soc <= 0:
                break

        cls._apply_pybamm_if_available(profile, start_soc / 100.0)
        return profile

    @classmethod
    def _apply_pybamm_if_available(cls, profile, initial_soc_frac: float):
        """
        If PyBaMM is available, re-run the drive cycle through the physics model
        and overwrite voltage, SOC, and temperature in the profile data points.
        """
        if not PYBAMM_AVAILABLE:
            return

        try:
            times = [dp.time_s for dp in profile.data_points]
            currents = [dp.current_A for dp in profile.data_points]

            model = PyBaMMBatteryModel(
                num_cells_series=20,
                capacity_ah=cls.PACK_CAPACITY_AH,
                initial_soc=initial_soc_frac
            )

            result = model.simulate_drive_cycle(
                times, currents,
                ambient_temp_C=cls.AMBIENT_TEMP_C
            )

            # If solver failed, result is None — keep fallback values
            if result is None:
                return

            # Overwrite data points with physics-based values
            for i, dp in enumerate(profile.data_points):
                dp.voltage_V = round(result['voltage'][i], 1)
                dp.soc_pct = round(result['soc'][i], 1)
                dp.temperature_C = round(result['temperature'][i], 1)

        except Exception as e:
            print(f"[PyBaMM] Failed to apply model, keeping fallback values: {e}")

    @classmethod
    def get_available_profiles(cls) -> List[dict]:
        """List of available profile generators with metadata"""
        return [
            {
                "name": "WMTC Part 1 — Urban (10 min)",
                "generator": cls.generate_wmtc_class1,
                "kwargs": {"parts": [1]},
                "description": "WMTC Class 1-2 Part 1: standardized urban cycle, 600s, deterministic"
            },
            {
                "name": "WMTC Part 1+2 — Urban+Rural (20 min)",
                "generator": cls.generate_wmtc_class1,
                "kwargs": {"parts": [1, 2]},
                "description": "WMTC Class 1-2 Part 1+2: urban + extra-urban, 1200s, deterministic"
            },
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


def encode_bms_temp_frame(dp: TripDataPoint) -> List[int]:
    """
    Encode BMS temperature data into 8 CAN bytes for BMS_TEMP (0x18F82880).

    Layout (BIG-ENDIAN):
      Byte 0-1: Mean Temp (16-bit, offset -40°C, 1°C/bit)
      Byte 2-3: Max Temp (16-bit, offset -40°C, 1°C/bit)
      Byte 4: Max Cell Nr (8-bit, 1-20)
      Byte 5-6: Min Temp (16-bit, offset -40°C, 1°C/bit)
      Byte 7: Min Cell Nr (8-bit, 1-20)
    """
    avg_temp = dp.temperature_C
    temp_max = avg_temp + random.uniform(0.5, 2.0)
    temp_min = avg_temp - random.uniform(0.5, 2.0)

    # Assign arbitrary, non-overlapping cell numbers (1 to 20) for realism
    max_cell = random.choice([5, 8, 12, 18])
    min_cell = random.choice([1, 2, 19, 20])

    # Convert to 16-bit with -40°C offset, factor 1
    avg_raw = int(round(avg_temp)) + 40
    max_raw = int(round(temp_max)) + 40
    min_raw = int(round(temp_min)) + 40

    # Clamp to 16-bit unsigned range
    avg_raw = max(0, min(avg_raw, 0xFFFF))
    max_raw = max(0, min(max_raw, 0xFFFF))
    min_raw = max(0, min(min_raw, 0xFFFF))

    data = [
        (avg_raw >> 8) & 0xFF, avg_raw & 0xFF,       # Byte 0-1: Mean
        (max_raw >> 8) & 0xFF, max_raw & 0xFF,       # Byte 2-3: Max Temp
        max_cell,                                    # Byte 4: Max Cell Nr
        (min_raw >> 8) & 0xFF, min_raw & 0xFF,       # Byte 5-6: Min Temp
        min_cell,                                    # Byte 7: Min Cell Nr
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
        self._suppressed_ids: set = set()  # CAN IDs suppressed by attack generator

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
                self._timer = None
            self.progress_changed.emit(100)
            self.simulation_finished.emit()
            self.status_message.emit(f"✓ Simulation complete: {self._profile.name}")
            return

        dp = self._profile.data_points[self._current_index]

        # Encode and send BMS frame (unless suppressed by attack generator)
        if BMS_CAN_ID not in self._suppressed_ids:
            bms_data = encode_bms_frame(dp)
            self._can_manager.send_message(BMS_CAN_ID, bms_data, is_extended=True, silent=True)

        # Encode and send MCU frame
        if MCU_CAN_ID not in self._suppressed_ids:
            mcu_data = encode_mcu_frame(dp)
            self._can_manager.send_message(MCU_CAN_ID, mcu_data, is_extended=True, silent=True)

        # Encode and send BMS Temperature frame
        if BMS_TEMP_CAN_ID not in self._suppressed_ids:
            bms_temp_data = encode_bms_temp_frame(dp)
            self._can_manager.send_message(BMS_TEMP_CAN_ID, bms_temp_data, is_extended=True, silent=True)

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
            'temperature': dp.temperature_C,
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

    # === Fault Injection Integration ===

    def suppress_id(self, can_id: int):
        """Suppress a CAN ID from being sent by the simulator (used by attack generator)."""
        self._suppressed_ids.add(can_id)

    def unsuppress_id(self, can_id: int):
        """Resume sending a previously suppressed CAN ID."""
        self._suppressed_ids.discard(can_id)

    def clear_suppressions(self):
        """Remove all CAN ID suppressions."""
        self._suppressed_ids.clear()

    @property
    def suppressed_ids(self) -> set:
        return self._suppressed_ids.copy()

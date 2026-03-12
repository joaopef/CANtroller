"""Tests for CAN frame encoding functions in simulator.py"""
import pytest
import sys
import os

# Ensure src is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from simulator import (
    TripDataPoint, TripProfileGenerator,
    encode_bms_frame, encode_mcu_frame, encode_bms_temp_frame,
)


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def nominal_dp():
    """A typical mid-ride data point."""
    return TripDataPoint(
        time_s=120.0,
        voltage_V=72.5,
        current_A=25.0,
        soc_pct=75.0,
        soh_pct=95.0,
        fc_cycles=120,
        speed_kmh=45,
        total_mileage_km=1250,
        current_mileage_km=5.3,
        gear=2,
        temperature_C=32.5,
    )


@pytest.fixture
def zero_dp():
    """Stationary data point with everything at minimum."""
    return TripDataPoint(
        time_s=0.0,
        voltage_V=60.0,
        current_A=0.0,
        soc_pct=0.0,
        soh_pct=100.0,
        fc_cycles=0,
        speed_kmh=0,
        total_mileage_km=0,
        current_mileage_km=0.0,
        gear=0,
        temperature_C=25.0,
    )


@pytest.fixture
def full_dp():
    """Data point at maximum values."""
    return TripDataPoint(
        time_s=0.0,
        voltage_V=84.0,
        current_A=110.0,
        soc_pct=100.0,
        soh_pct=100.0,
        fc_cycles=65535,
        speed_kmh=255,
        total_mileage_km=16777215,
        current_mileage_km=255.0,
        gear=5,
        temperature_C=55.0,
    )


# ── encode_bms_frame ─────────────────────────────────────────

class TestEncodeBmsFrame:
    def test_returns_8_bytes(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        assert len(data) == 8

    def test_all_bytes_in_range(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        assert all(0 <= b <= 255 for b in data)

    def test_voltage_encoding(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        v_raw = (data[0] << 8) | data[1]
        decoded_voltage = v_raw * 0.1
        assert abs(decoded_voltage - nominal_dp.voltage_V) < 0.1

    def test_current_encoding(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        c_raw = (data[2] << 8) | data[3]
        decoded_current = c_raw * 0.05
        assert abs(decoded_current - abs(nominal_dp.current_A)) < 0.05

    def test_soc_encoding(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        assert data[4] == 75  # SOC = 75%

    def test_soh_encoding(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        assert data[5] == 95  # SOH = 95%

    def test_fc_cycles_encoding(self, nominal_dp):
        data = encode_bms_frame(nominal_dp)
        fc = (data[6] << 8) | data[7]
        assert fc == 120

    def test_zero_values(self, zero_dp):
        data = encode_bms_frame(zero_dp)
        assert data[4] == 0   # SOC
        assert (data[6] << 8 | data[7]) == 0  # FC cycles

    def test_max_values(self, full_dp):
        data = encode_bms_frame(full_dp)
        assert data[4] == 100  # SOC clamped to 100
        assert data[5] == 100  # SOH
        assert (data[6] << 8 | data[7]) == 65535  # FC max


# ── encode_mcu_frame ─────────────────────────────────────────

class TestEncodeMcuFrame:
    def test_returns_8_bytes(self, nominal_dp):
        data = encode_mcu_frame(nominal_dp)
        assert len(data) == 8

    def test_speed_encoding(self, nominal_dp):
        data = encode_mcu_frame(nominal_dp)
        assert data[0] == 45

    def test_total_mileage_encoding(self, nominal_dp):
        data = encode_mcu_frame(nominal_dp)
        total = (data[1] << 16) | (data[2] << 8) | data[3]
        assert total == 1250

    def test_current_mileage_encoding(self, nominal_dp):
        data = encode_mcu_frame(nominal_dp)
        assert data[4] == 5  # round(5.3) = 5

    def test_gear_encoding(self, nominal_dp):
        data = encode_mcu_frame(nominal_dp)
        gear = (data[5] >> 5) & 0x07
        assert gear == 2

    def test_gear_max(self, full_dp):
        data = encode_mcu_frame(full_dp)
        gear = (data[5] >> 5) & 0x07
        assert gear == 5

    def test_speed_zero(self, zero_dp):
        data = encode_mcu_frame(zero_dp)
        assert data[0] == 0

    def test_flags_always_zero(self, nominal_dp):
        data = encode_mcu_frame(nominal_dp)
        assert data[6] == 0
        assert data[7] == 0


# ── encode_bms_temp_frame ────────────────────────────────────

class TestEncodeBmsTempFrame:
    def test_returns_8_bytes(self, nominal_dp):
        data = encode_bms_temp_frame(nominal_dp)
        assert len(data) == 8

    def test_avg_temp_encoding(self, nominal_dp):
        data = encode_bms_temp_frame(nominal_dp)
        avg_raw = (data[0] << 8) | data[1]
        decoded = avg_raw - 40
        # Allow ±1°C rounding
        assert abs(decoded - nominal_dp.temperature_C) <= 1.0

    def test_max_temp_greater_than_avg(self, nominal_dp):
        data = encode_bms_temp_frame(nominal_dp)
        avg = (data[0] << 8) | data[1]
        max_t = (data[2] << 8) | data[3]
        assert max_t >= avg

    def test_min_temp_less_than_avg(self, nominal_dp):
        data = encode_bms_temp_frame(nominal_dp)
        avg = (data[0] << 8) | data[1]
        min_t = (data[5] << 8) | data[6]
        assert min_t <= avg

    def test_cell_numbers_in_range(self, nominal_dp):
        data = encode_bms_temp_frame(nominal_dp)
        assert 1 <= data[4] <= 20   # Max cell nr
        assert 1 <= data[7] <= 20   # Min cell nr


# ── Trip Profile Generation ──────────────────────────────────

class TestTripProfileGeneration:
    def test_city_trip_creates_datapoints(self):
        profile = TripProfileGenerator.generate_city_trip(duration_min=1)
        assert len(profile.data_points) > 0

    def test_city_trip_soc_decreases(self):
        profile = TripProfileGenerator.generate_city_trip(
            duration_min=5, start_soc=90.0)
        first = profile.data_points[0]
        last = profile.data_points[-1]
        assert last.soc_pct < first.soc_pct

    def test_highway_trip_higher_speed(self):
        profile = TripProfileGenerator.generate_highway_trip(duration_min=2)
        speeds = [dp.speed_kmh for dp in profile.data_points]
        avg_speed = sum(speeds) / len(speeds)
        assert avg_speed > 30  # Highway should average well above city

    def test_voltage_from_soc_range(self):
        # Full SOC → ~84V
        v_full = TripProfileGenerator._voltage_from_soc(100.0)
        assert 82.0 <= v_full <= 85.0
        # Empty SOC → ~60V
        v_empty = TripProfileGenerator._voltage_from_soc(0.0)
        assert 59.0 <= v_empty <= 62.0
        # Monotonic: higher SOC → higher voltage
        v_mid = TripProfileGenerator._voltage_from_soc(50.0)
        assert v_empty < v_mid < v_full

    def test_available_profiles(self):
        profiles = TripProfileGenerator.get_available_profiles()
        assert len(profiles) >= 5
        names = [p['name'] for p in profiles]
        assert any('City' in n for n in names)
        assert any('Highway' in n for n in names)

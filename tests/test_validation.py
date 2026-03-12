"""Tests for input validation in the attack generator / collection dialog."""
import os
import sys
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from dialogs.collection_dialog import CollectionConfigDialog, ATTACK_DEFAULTS
from dialogs.scenario_dialog import ScenarioBuilderDialog, DEFAULT_DURATIONS, STEP_TYPES


class TestCollectionConfigDefaults:
    """Verify default configuration values."""

    def test_all_attacks_have_defaults(self):
        expected_keys = {'dos', 'injection', 'fuzzing', 'replay', 'suspension', 'masquerade'}
        assert set(ATTACK_DEFAULTS.keys()) == expected_keys

    def test_all_defaults_enabled(self):
        for key, cfg in ATTACK_DEFAULTS.items():
            assert cfg['enabled'] is True, f"{key} should be enabled by default"

    def test_all_durations_positive(self):
        for key, cfg in ATTACK_DEFAULTS.items():
            assert cfg['duration'] > 0, f"{key} duration should be positive"


class TestScenarioStepTypes:
    """Verify scenario builder step type constants."""

    def test_step_types_include_attacks(self):
        for atk in ['dos', 'injection', 'fuzzing', 'replay', 'masquerade', 'suspension']:
            assert atk in STEP_TYPES

    def test_step_types_include_normal_and_pause(self):
        assert 'normal' in STEP_TYPES
        assert 'pause' in STEP_TYPES

    def test_default_durations_for_all_types(self):
        for t in STEP_TYPES:
            assert t in DEFAULT_DURATIONS
            assert DEFAULT_DURATIONS[t] > 0


class TestScenarioJsonRoundtrip:
    """Test that a scenario can be saved and loaded from JSON."""

    def test_save_load_roundtrip(self, tmp_path):
        steps = [
            {'type': 'normal', 'duration': 30, 'notes': 'baseline'},
            {'type': 'dos', 'duration': 10, 'notes': 'flood test'},
            {'type': 'normal', 'duration': 30, 'notes': 'recovery'},
        ]
        scenario = {'repeat': 2, 'steps': steps}

        filepath = str(tmp_path / "test_scenario.json")
        with open(filepath, 'w') as f:
            json.dump(scenario, f)

        with open(filepath, 'r') as f:
            loaded = json.load(f)

        assert loaded['repeat'] == 2
        assert len(loaded['steps']) == 3
        assert loaded['steps'][1]['type'] == 'dos'
        assert loaded['steps'][1]['duration'] == 10

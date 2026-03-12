"""Tests for the labeled logger CSV output."""
import csv
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from labeled_logger import LabeledLogger


@pytest.fixture
def logger():
    lg = LabeledLogger(buffer_size=100)
    yield lg
    if lg.is_running:
        lg.stop()


@pytest.fixture
def tmp_csv(tmp_path):
    return str(tmp_path / "test_log.csv")


class TestLabeledLogger:
    def test_start_creates_file(self, logger, tmp_csv):
        logger.start(tmp_csv)
        assert os.path.exists(tmp_csv)
        logger.stop()

    def test_header_written(self, logger, tmp_csv):
        logger.start(tmp_csv)
        logger.stop()
        with open(tmp_csv, 'r') as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == LabeledLogger.HEADER

    def test_log_tx_increments_counter(self, logger, tmp_csv):
        logger.start(tmp_csv)
        logger.log_tx(0x100, [0x01, 0x02], False, label="dos", subtype="flood")
        assert logger.tx_count == 1
        assert logger.rx_count == 0
        logger.stop()

    def test_log_tx_writes_csv_row(self, logger, tmp_csv):
        logger.start(tmp_csv)
        logger.log_tx(0x000, [0xAA] * 8, False, label="dos", subtype="flood")
        logger.stop()  # flushes buffer

        with open(tmp_csv, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            row = next(reader)

        assert row[1] == '0x00000000'     # CAN ID
        assert row[2] == '0'              # not extended
        assert row[3] == '8'              # DLC
        assert 'AA' in row[4]             # data hex
        assert row[5] == 'TX'             # direction
        assert row[6] == 'dos'            # label
        assert row[7] == 'flood'          # subtype

    def test_set_label_affects_subsequent_logs(self, logger, tmp_csv):
        logger.start(tmp_csv)
        logger.set_label("injection", "soc_spoof")
        logger.log_tx(0x18F81280, [0] * 8, True)
        logger.stop()

        with open(tmp_csv, 'r') as f:
            reader = csv.reader(f)
            next(reader)
            row = next(reader)

        assert row[6] == 'injection'
        assert row[7] == 'soc_spoof'

    def test_reset_label(self, logger, tmp_csv):
        logger.start(tmp_csv)
        logger.set_label("fuzzing", "random")
        logger.reset_label()
        logger.log_tx(0x100, [0] * 8, False)
        logger.stop()

        with open(tmp_csv, 'r') as f:
            reader = csv.reader(f)
            next(reader)
            row = next(reader)

        assert row[6] == 'normal'
        assert row[7] == ''

    def test_total_count(self, logger, tmp_csv):
        logger.start(tmp_csv)
        logger.log_tx(0x100, [0] * 8, False)
        logger.log_tx(0x200, [0] * 8, False)
        assert logger.total_count == 2
        logger.stop()

    def test_stop_flushes_all(self, logger, tmp_csv):
        logger.start(tmp_csv)
        for i in range(50):
            logger.log_tx(i, [0] * 8, False)
        logger.stop()

        with open(tmp_csv, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # header
            rows = list(reader)
        assert len(rows) == 50

    def test_multiple_start_stop_cycles(self, logger, tmp_csv):
        """Logger can be started and stopped multiple times."""
        logger.start(tmp_csv)
        logger.log_tx(0x100, [0] * 8, False)
        logger.stop()

        # Second cycle to a different file
        tmp_csv2 = tmp_csv.replace('.csv', '_2.csv')
        logger.start(tmp_csv2)
        logger.log_tx(0x200, [0] * 8, False)
        logger.stop()

        assert os.path.exists(tmp_csv)
        assert os.path.exists(tmp_csv2)

    def test_is_running_property(self, logger, tmp_csv):
        assert not logger.is_running
        logger.start(tmp_csv)
        assert logger.is_running
        logger.stop()
        assert not logger.is_running

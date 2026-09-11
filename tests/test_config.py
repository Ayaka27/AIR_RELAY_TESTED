import json

import pytest

from airrelay.core.config import (ConfigManager, ConfigError, normalize_freq,
                                  validate_freq_hz)
from airrelay.util.paths import resolve_paths


def _cfg(home):
    return ConfigManager(resolve_paths(home).config_file)


def test_normalize_freq_no_float_loss():
    assert normalize_freq(145.500) == 145_500_000
    assert normalize_freq(433.250) == 433_250_000
    assert normalize_freq(446.125) == 446_125_000


def test_validate_out_of_band(home):
    with pytest.raises(ConfigError):
        validate_freq_hz(10_000_000, {"min_mhz": 24, "max_mhz": 1700})
    validate_freq_hz(145_000_000, {"min_mhz": 24, "max_mhz": 1700})


def test_apply_channels_persists(home):
    paths = resolve_paths(home)
    cfg = ConfigManager(paths.config_file)
    chans = [
        {"id": "F1", "frequency_mhz": 144.390, "enabled": True,
         "tx_allowed": True, "rx_allowed": True},
        {"id": "F2", "frequency_mhz": 432.600, "enabled": True,
         "tx_allowed": True, "rx_allowed": True},
    ]
    cfg.apply_channels(chans)
    # new instance reads the same persisted file
    cfg2 = ConfigManager(paths.config_file)
    got = {c.id: c.freq_mhz for c in cfg2.channels()}
    assert got["F1"] == 144.390
    assert got["F2"] == 432.600


def test_duplicate_frequency_rejected(home):
    cfg = _cfg(home)
    chans = [
        {"id": "F1", "frequency_mhz": 145.5, "enabled": True},
        {"id": "F2", "frequency_mhz": 145.5, "enabled": True},
    ]
    with pytest.raises(ConfigError):
        cfg.apply_channels(chans)


def test_invalid_frequency_rejected(home):
    cfg = _cfg(home)
    with pytest.raises(ConfigError):
        cfg.apply_channels([{"id": "F1", "frequency_mhz": 0.5, "enabled": True}])


def test_scalar_runtime_set(home):
    cfg = _cfg(home)
    cfg.set("radio.tx_enable", False)
    assert cfg.get("radio.tx_enable") is False
    with pytest.raises(ConfigError):
        cfg.set("store_forward.max_retry_count", 1_000_000)

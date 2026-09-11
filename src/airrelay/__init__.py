"""Airborne multi-band SDR relay system.

Headless store-and-forward radio relay running on a Raspberry Pi 4,
bridging four configurable radio channels via an RTL-SDR (receiver/scan
path) and a HackRF One (half-duplex transmit path), with optional Pixhawk
integration and automatic altitude optimization.

See /docs for architecture and operational documentation.
"""

__version__ = "1.0.0"
APP_NAME = "airrelay"

"""Command-line entry points.

    airrelay serve                run the full system (radios, relay, web)
    airrelay config-show          print effective configuration
    airrelay channels             show active channel configuration
    airrelay diagnose             report device + store health
    airrelay dump-queue [n]       dump queue messages as JSON to stdout

Runs all subsystems in one process (see web/context.py).
"""

from __future__ import annotations

import argparse
import json
import sys

from .core.config import ConfigManager
from .util.paths import resolve_paths


def _paths():
    return resolve_paths()


def cmd_serve(_args) -> None:
    from .web.context import AppContext
    from .web.app import create_app
    ctx = AppContext()
    ctx.register_config_hook()
    ctx.start()
    app = create_app(ctx)
    host = str(ctx.config.get("dashboard.host", "0.0.0.0"))
    port = int(ctx.config.get("dashboard.port", 8080))
    print(f"[airrelay] dashboard http://{host}:{port}", flush=True)
    app.run(host=host, port=port, threaded=True, debug=False)


def cmd_config_show(_args) -> None:
    cfg = ConfigManager(_paths().config_file)
    print(json.dumps(cfg.snapshot(), indent=2))


def cmd_channels(_args) -> None:
    cfg = ConfigManager(_paths().config_file)
    for c in cfg.channels():
        print(f"{c.id:4} {'ON ' if c.enabled else 'OFF'} "
              f"{c.freq_mhz:>9.4f} MHz   TX={c.tx_allowed} RX={c.rx_allowed}")


def cmd_diagnose(_args) -> None:
    from .web.context import AppContext
    ctx = AppContext()
    d = ctx.radio.device_status()
    st = ctx.store.stats()
    print(json.dumps({
        "device": d,
        "store": st,
        "config_file": str(ctx.paths.config_file),
        "db": str(ctx.paths.db_path),
        "log_dir": str(ctx.paths.log_dir),
    }, indent=2))


def cmd_dump_queue(args) -> None:
    from .store.queue_db import MessageStore
    store = MessageStore(_paths().db_path)
    limit = int(args.limit)
    msgs = store.queue_view()
    print(json.dumps(msgs[:limit], indent=2))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="airrelay")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the full relay system + dashboard")
    sub.add_parser("config-show", help="print effective configuration")
    sub.add_parser("channels", help="show channels")
    sub.add_parser("diagnose", help="hardware/store diagnostics")
    dq = sub.add_parser("dump-queue", help="dump queued messages")
    dq.add_argument("limit", nargs="?", default=500, type=int)
    args = p.parse_args(argv)
    {
        "serve": cmd_serve,
        "config-show": cmd_config_show,
        "channels": cmd_channels,
        "diagnose": cmd_diagnose,
        "dump-queue": cmd_dump_queue,
    }[args.cmd](args)


if __name__ == "__main__":
    main(sys.argv[1:])

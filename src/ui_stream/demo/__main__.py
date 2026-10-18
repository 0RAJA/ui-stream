"""Start the local-only demo using an explicit SQLite path and no external dependencies."""

import argparse
import logging
from pathlib import Path

from ui_stream.demo.app import DemoService, make_server
from ui_stream.demo.storage import DatabaseInUse


def main():
    parser = argparse.ArgumentParser(description="Local SQLite UI stream demo (macOS/Linux)")
    parser.add_argument("--database", type=Path, required=True, help="Dedicated demo SQLite file")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between synthetic progress events")
    args = parser.parse_args()
    if not 0 < args.port < 65536 or not 0 < args.interval <= 5:
        parser.error("port must be 1..65535 and interval must be greater than 0 and at most 5")
    logging.basicConfig(level=logging.WARNING)
    try:
        service = DemoService(args.database, interval=args.interval)
    except DatabaseInUse as error:
        parser.error(str(error))
    try:
        server = make_server(service, port=args.port)
        print(f"UI stream demo: http://127.0.0.1:{server.server_port}", flush=True)
        print("Synthetic local data only. Press Ctrl-C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        service.close()


if __name__ == "__main__":
    main()

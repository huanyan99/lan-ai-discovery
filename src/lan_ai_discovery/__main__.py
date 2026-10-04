"""CLI entry point: ``python -m lan_ai_discovery`` (thin argparse shell).

Two primitives only:

* ``announce`` — advertise local AI endpoint(s) from config / env
* ``browse``   — discover local AI endpoints on this LAN
"""

from __future__ import annotations

import argparse
import json
import logging

from . import __version__
from .app import DiscoveryService
from .browser import discover


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lan-ai-discovery",
        description="Advertise and discover local AI APIs over mDNS",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    announce = sub.add_parser("announce", help="Advertise local AI endpoint(s) from config")
    announce.add_argument("--config", help="YAML config file")
    announce.add_argument("-v", "--verbose", action="store_true")

    browse = sub.add_parser("browse", help="Discover local AI endpoints on this LAN")
    browse.add_argument("--timeout", type=float, default=3.0, help="Seconds to listen")
    browse.add_argument("--vendor", help="Filter by vendor hint")
    browse.add_argument("--api", help="Filter by API dialect (openai, anthropic)")
    browse.add_argument("--status", help="Filter by status (up, degraded, down)")
    browse.add_argument("--json", action="store_true", help="Output JSON")
    browse.add_argument("-v", "--verbose", action="store_true")

    return parser


def _cmd_announce(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    def _on_status(event: str, detail: str) -> None:
        logging.info("[%s] %s", event, detail)

    service = DiscoveryService.from_config_file(args.config, on_status=_on_status)
    try:
        service.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_browse(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    results = discover(
        timeout_s=args.timeout,
        vendor=args.vendor,
        api=args.api,
        status=args.status,
    )
    if args.json:
        payload = [
            {
                "name": s.name,
                "host": s.host,
                "port": s.port,
                "addresses": list(s.addresses),
                "api": s.api,
                "auth": s.auth,
                "base_path": s.base_path,
                "models_path": s.models_path,
                "vendor": s.vendor,
                "status": s.status,
                "instance_id": s.instance_id,
                "base_url": s.base_url,
                "models_list": list(s.models_list),
                "label": s.label,
            }
            for s in results
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not results:
        print("No local AI services found.")
        return 1
    print(f"Found {len(results)} service(s):\n")
    for s in results:
        mark = "●" if s.is_up else "○"
        print(f"  {mark} {s.name}")
        print(f"      URL      : {s.base_url}")
        print(f"      Vendor   : {s.vendor}   API: {s.api}   Auth: {s.auth}")
        print(f"      Status   : {s.status}   Models: {s.models_url}")
        if s.models_list:
            print(f"      Models   : {', '.join(s.models_list)}")
        if s.label:
            print(f"      Label    : {s.label}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "announce":
        return _cmd_announce(args)
    if args.command == "browse":
        return _cmd_browse(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

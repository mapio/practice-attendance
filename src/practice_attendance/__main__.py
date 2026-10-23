"""`practice-attendance serve|urls`."""

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from .server import create_app
from .team import ConfigError, parse
from .tokens import player_token, read_salt, staff_token


def main(argv=None):
    parser = argparse.ArgumentParser(prog="practice-attendance", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "urls"):
        command = commands.add_parser(name)
        command.add_argument("--team", type=Path, default=Path("teams/team.json"))
        if name == "serve":
            command.add_argument("--data", type=Path, default=Path("data"))
            command.add_argument("--logs", type=Path, default=Path("logs"))
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8000)
            command.add_argument(
                "--proxy-headers", action="store_true", help="trust X-Forwarded-For"
            )
        else:
            command.add_argument("--base", default="http://127.0.0.1:8000")
            command.add_argument(
                "--players", action="store_true", help="also each player's link"
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "urls":
            salt = read_salt(args.team.with_name("salt"))
            base = args.base.rstrip("/")
            print(f"{base}/{staff_token(salt)}")
            if args.players:
                team = parse(json.loads(args.team.read_text(encoding="utf-8")))
                for player, name in team.roster.items():
                    print(f"{name}: {base}/{player_token(salt, player)}")
            return 0
        args.logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            args.logs / "app.log", maxBytes=1_000_000, backupCount=3
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger("practice-attendance").addHandler(handler)
        app = create_app(args.team, args.data)
    except (OSError, ValueError, ConfigError) as error:
        print(f"practice-attendance: {error}", file=sys.stderr)
        return 1
    # no access log: the path of a request holds the token
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        proxy_headers=args.proxy_headers,
        forwarded_allow_ips="*" if args.proxy_headers else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

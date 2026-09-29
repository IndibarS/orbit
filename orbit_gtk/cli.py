"""Nala-style entry points into reviewed GUI workflows; no shell execution."""

import argparse
from pathlib import Path


def parse_command(arguments, cwd=None):
    if len(arguments) == 1 and arguments[0].endswith(".deb"):
        arguments = ["install-local", arguments[0]]
    parser = argparse.ArgumentParser(
        prog="orbit-gtk", description="Open Orbit and review package operations in the GUI."
    )
    parser.add_argument("--version", action="version", version="Orbit 0.2.0")
    commands = parser.add_subparsers(dest="command")
    for name in ("update", "upgrade", "full-upgrade", "autoremove", "clean", "history", "fetch"):
        commands.add_parser(name)
    alias = commands.add_parser("dist-upgrade")
    alias.set_defaults(command="full-upgrade")
    for name in ("install", "remove", "purge", "reinstall"):
        commands.add_parser(name).add_argument("packages", nargs="+")
    for name in ("show", "info"):
        item = commands.add_parser(name)
        item.add_argument("package")
        item.set_defaults(command="show")
    commands.add_parser("search").add_argument("query", nargs="+")
    listing = commands.add_parser("list")
    options = listing.add_mutually_exclusive_group()
    options.add_argument("--installed", action="store_true")
    options.add_argument("--upgradable", action="store_true")
    listing.add_argument("query", nargs="?", default="")
    commands.add_parser("install-local").add_argument("path")
    args = parser.parse_args(arguments)
    if args.command == "list" and args.upgradable and args.query:
        parser.error("Use list --upgradable without a search term to open Updates.")
    if args.command == "install":
        local = [name for name in args.packages if name.endswith(".deb") or "/" in name]
        if local:
            if len(args.packages) != 1:
                parser.error(
                    "Install one local .deb at a time; do not mix archives and repository packages."
                )
            args.command, args.path = "install-local", local[0]
    if args.command == "install-local":
        path = Path(args.path).expanduser()
        if not path.is_absolute():
            path = Path(cwd or Path.cwd()) / path
        args.path = str(path.absolute())
        if path.suffix != ".deb":
            parser.error("Local installation requires a .deb archive.")
    return args

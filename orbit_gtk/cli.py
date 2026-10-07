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
    for name in (
        "update",
        "upgrade",
        "full-upgrade",
        "autoremove",
        "autopurge",
        "purge-config",
        "fix-broken",
        "clean",
        "history",
        "fetch",
    ):
        commands.add_parser(name)
    manual = commands.choices["fetch"].add_mutually_exclusive_group()
    for provider in ("debian", "ubuntu", "devuan", "linuxmint", "kali"):
        manual.add_argument("--" + provider, metavar="SUITE")
    commands.choices["fetch"].add_argument("--sources", action="store_true")
    commands.choices["fetch"].add_argument("--components", nargs="+")
    commands.choices["fetch"].add_argument("--fetches", type=int, choices=range(1, 17), default=3)
    commands.choices["fetch"].add_argument("--https-only", action="store_true")
    commands.choices["fetch"].add_argument("--country", "-c", action="append", default=[])
    alias = commands.add_parser("dist-upgrade")
    alias.set_defaults(command="full-upgrade")
    for name in ("install", "remove", "purge", "reinstall"):
        commands.add_parser(name).add_argument("packages", nargs="+")
    for name in ("show", "info"):
        item = commands.add_parser(name)
        item.add_argument("package")
        item.set_defaults(command="show")
    search = commands.add_parser("search")
    search.add_argument("query", nargs="+")
    search.add_argument("--mode", choices=("text", "glob", "regex"), default="text")
    search.add_argument("--names", action="store_true")
    search.add_argument(
        "--filter", choices=("all", "installed", "upgradable", "virtual"), default="all"
    )
    commands.choices["history"].add_argument(
        "history_action", nargs="?", choices=("info", "undo", "redo")
    )
    commands.choices["history"].add_argument("history_id", nargs="?", default="last")
    listing = commands.add_parser("list")
    options = listing.add_mutually_exclusive_group()
    options.add_argument("--installed", action="store_true")
    options.add_argument("--upgradable", action="store_true")
    listing.add_argument("query", nargs="?", default="")
    commands.add_parser("install-local").add_argument("path")
    remote = commands.add_parser("install-url")
    remote.add_argument("url")
    remote.add_argument("--sha256")
    for name, command in commands.choices.items():
        if name in {
            "install",
            "remove",
            "purge",
            "reinstall",
            "upgrade",
            "full-upgrade",
            "dist-upgrade",
            "autoremove",
            "autopurge",
            "purge-config",
            "fix-broken",
        }:
            command.add_argument("--download-only", action="store_true")
            command.add_argument("--interactive", action="store_true")
            command.add_argument("--update", dest="refresh", action="store_true")
            command.add_argument("--target-release")
            command.add_argument("--exclude", action="append", default=[])
            command.add_argument(
                "--install-recommends", action=argparse.BooleanOptionalAction, default=None
            )
            command.add_argument(
                "--install-suggests", action=argparse.BooleanOptionalAction, default=None
            )
            command.add_argument("--conffile", choices=("keep", "replace", "ask"), default=None)
    args = parser.parse_args(arguments)
    args.options = {}
    for name in (
        "download_only",
        "refresh",
        "target_release",
        "exclude",
        "conffile",
        "interactive",
    ):
        value = getattr(args, name, None)
        if value:
            args.options[name] = value
    for name in ("recommends", "suggests"):
        value = getattr(args, "install_" + name, None)
        if value is not None:
            args.options[name] = value
    if args.command == "install":
        versions = {}
        packages = []
        for spec in args.packages:
            if "=" in spec and not spec.startswith(("http://", "https://")):
                name, version = spec.split("=", 1)
                versions[name] = version
                packages.append(name)
            else:
                packages.append(spec)
        args.packages = packages
        if versions:
            args.options["versions"] = versions
    if args.command == "list" and args.upgradable and args.query:
        parser.error("Use list --upgradable without a search term to open Updates.")
    if args.command == "install":
        remote = [name for name in args.packages if name.startswith(("http://", "https://"))]
        if remote:
            if len(args.packages) != 1:
                parser.error(
                    "Download URL packages individually before combining them with local archives."
                )
            args.command, args.url, args.sha256 = "install-url", remote[0], None
        else:
            local = [name for name in args.packages if name.endswith(".deb") or "/" in name]
            if len(local) == 1 and len(args.packages) == 1:
                args.command, args.path = "install-local", local[0]
            elif local:
                args.command = "install-batch"
                args.paths = [
                    str((Path(cwd or Path.cwd()) / Path(name).expanduser()).absolute())
                    for name in local
                ]
                args.packages = [name for name in args.packages if name not in local]
    if args.command == "install-local":
        path = Path(args.path).expanduser()
        if not path.is_absolute():
            path = Path(cwd or Path.cwd()) / path
        args.path = str(path.absolute())
        if path.suffix != ".deb":
            parser.error("Local installation requires a .deb archive.")
    return args

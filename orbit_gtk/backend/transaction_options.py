"""Validated, explicit transaction options shared by the CLI and root helper."""

import json

BOOL_OPTIONS = {
    "download_only",
    "refresh",
    "recommends",
    "suggests",
    "purge",
    "autoremove",
    "interactive",
}


def validate_options(options=None):
    options = dict(options or {})
    unknown = (
        options.keys()
        - BOOL_OPTIONS
        - {"versions", "target_release", "exclude", "conffile", "requests"}
    )
    if unknown:
        raise ValueError(f"Unknown transaction options: {', '.join(sorted(unknown))}")
    for key in BOOL_OPTIONS & options.keys():
        if type(options[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    requests = options.get("requests", [])
    if not isinstance(requests, list) or any(
        not isinstance(r, dict)
        or set(r) - {"action", "name", "version", "expected"}
        or r.get("action") not in {"install", "remove", "purge", "reinstall"}
        or not isinstance(r.get("name"), str)
        for r in requests
    ):
        raise ValueError("Invalid package requests")
    if len(requests) > 10000:
        raise ValueError("Too many package requests")
    versions = options.get("versions", {})
    if not isinstance(versions, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v or any(c.isspace() for c in v)
        for k, v in versions.items()
    ):
        raise ValueError("Versions must map package names to exact versions")
    excludes = options.get("exclude", [])
    if not isinstance(excludes, list) or any(not isinstance(v, str) or not v for v in excludes):
        raise ValueError("Exclusions must be package patterns")
    release = options.get("target_release")
    if release is not None and (
        not isinstance(release, str) or not release or any(c.isspace() for c in release)
    ):
        raise ValueError("Invalid target release")
    if options.get("conffile", "keep") not in {"keep", "replace", "ask"}:
        raise ValueError("Unknown configuration-file policy")
    return options


def option_arguments(options):
    return ["--options", json.dumps(validate_options(options))] if options else []

"""Turn historical package states into fresh reviewed requests, not filesystem rollback."""


def replay_requests(transaction, undo=False):
    if transaction.status != "Completed":
        raise ValueError("Only completed transactions can be replayed.")
    requests = []
    for action, field in (
        ("install", "installed_pkgs"),
        ("upgrade", "upgraded_pkgs"),
        ("downgrade", "downgraded_pkgs"),
        ("remove", "removed_pkgs"),
        ("purge", "purged_pkgs"),
        ("reinstall", "reinstalled_pkgs"),
    ):
        for package in getattr(transaction, field):
            old, new = package.installed_version, package.latest_version
            name = package.full_name or package.name
            if undo:
                if action == "install":
                    request = {"action": "remove", "name": name, "expected": new}
                else:
                    if not old:
                        raise ValueError(f"The previous version of {name} was not recorded.")
                    request = {
                        "action": "install",
                        "name": name,
                        "version": old,
                        "expected": None if action in {"remove", "purge"} else new,
                    }
            else:
                request = {
                    "action": action if action in {"remove", "purge", "reinstall"} else "install",
                    "name": name,
                }
                if request["action"] == "install":
                    if not new:
                        raise ValueError(f"The requested version of {name} was not recorded.")
                    request["version"] = new
            requests.append(request)
    if not requests:
        raise ValueError("This entry has no replayable package changes.")
    return requests

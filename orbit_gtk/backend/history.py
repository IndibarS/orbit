"""Read Nala and APT transaction history into Orbit's display model."""

from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from orbit_gtk.backend.models import HistoryTransaction, PackageInfo

LOG = logging.getLogger(__name__)
NALA_HISTORY_PATH = Path("/var/lib/nala/history.json")
APT_HISTORY_PATHS = (
    Path("/var/log/apt/history.log"),
    Path("/var/log/apt/history.log.1.gz"),
    Path("/var/log/apt/history.log.1"),
)
_APT_PACKAGE = re.compile(r"(?:^|, )([^,(]+?) \(([^,)]+)(?:, ([^)]+))?\)")


def _package_records(value: object, operation: str) -> list[PackageInfo]:
    """Convert the variable-length records written by Nala history safely."""
    records: list[PackageInfo] = []
    if not isinstance(value, list):
        return records
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2 or not item[0]:
            continue
        name, new_version = str(item[0]), str(item[1])
        previous = str(item[3]) if len(item) >= 4 else None
        size = 0
        if len(item) >= 3:
            try:
                size = int(item[2])
            except (TypeError, ValueError):
                # Nala's oldest history format used name, old, new, size.
                if len(item) >= 4:
                    previous, new_version = str(item[1]), str(item[2])
                    try:
                        size = int(item[3])
                    except (TypeError, ValueError):
                        size = 0
        if operation in {"upgrade", "downgrade"}:
            records.append(
                PackageInfo(
                    name=name,
                    installed_version=previous,
                    latest_version=new_version,
                    size_bytes=size,
                )
            )
        elif operation in {"remove", "purge"}:
            records.append(PackageInfo(name=name, installed_version=new_version, size_bytes=size))
        else:
            records.append(PackageInfo(name=name, latest_version=new_version, size_bytes=size))
    return records


def _load_nala_history(path: Path = NALA_HISTORY_PATH) -> list[HistoryTransaction]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        LOG.warning("Could not read Nala history at %s: %s", path, error)
        return []
    if not isinstance(data, dict):
        return []

    transactions: list[HistoryTransaction] = []
    for history_id, entry in data.items():
        if history_id in {"Nala", "History-Version", "User-Installed"} or not isinstance(
            entry, dict
        ):
            continue
        operation = str(entry.get("Operation", "upgrade")).lower()
        if entry.get("Purged") and operation in {"remove", "autoremove"}:
            operation = "purge"
        command = entry.get("Command", [])
        command_text = " ".join(map(str, command)) if isinstance(command, list) else str(command)
        upgraded = _package_records(entry.get("Upgraded"), "upgrade")
        installed = _package_records(entry.get("Installed"), "install")
        removed = _package_records(entry.get("Removed"), "remove")
        removed.extend(_package_records(entry.get("Auto-Removed"), "remove"))
        purged = removed if entry.get("Purged") is True else []
        if purged:
            removed = []
        reinstalled = _package_records(entry.get("Reinstalled"), "reinstall")
        downgraded = _package_records(entry.get("Downgraded"), "downgrade")
        total = sum(map(len, (upgraded, installed, removed, purged, reinstalled, downgraded)))
        try:
            altered = int(entry.get("Altered", total))
        except (ValueError, TypeError):
            altered = total
        transactions.append(
            HistoryTransaction(
                id=f"nala-{history_id}",
                date=str(entry.get("Date") or "Unknown date"),
                requested_by=str(entry.get("Requested-By") or "Unknown user"),
                command=command_text or f"nala {operation}",
                operation=operation,
                altered_count=altered,
                upgraded_pkgs=upgraded,
                installed_pkgs=installed,
                removed_pkgs=removed,
                purged_pkgs=purged,
                reinstalled_pkgs=reinstalled,
                downgraded_pkgs=downgraded,
            )
        )
    return transactions


def _read_history_text(path: Path) -> str:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as file:
                return file.read()
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _apt_packages(value: str, operation: str) -> list[PackageInfo]:
    packages: list[PackageInfo] = []
    for match in _APT_PACKAGE.finditer(value):
        name = match.group(1).strip()
        first_version = match.group(2).strip()
        second_version = (match.group(3) or "").strip()
        if operation in {"upgrade", "downgrade"}:
            packages.append(
                PackageInfo(
                    name=name, installed_version=first_version, latest_version=second_version
                )
            )
        elif operation in {"remove", "purge"}:
            packages.append(PackageInfo(name=name, installed_version=first_version))
        else:
            packages.append(PackageInfo(name=name, latest_version=first_version))
    return packages


def _load_apt_history(paths: tuple[Path, ...] = APT_HISTORY_PATHS) -> list[HistoryTransaction]:
    transactions: list[HistoryTransaction] = []
    counter = 0
    for path in reversed(paths):
        current: dict[str, str] = {}
        for line in _read_history_text(path).splitlines():
            if not line.strip():
                continue
            if ": " not in line:
                continue
            key, value = line.split(": ", 1)
            if key == "Start-Date":
                current = {"Start-Date": value}
                continue
            if not current:
                continue
            current[key] = value
            if key != "End-Date":
                continue
            upgraded = _apt_packages(current.get("Upgrade", ""), "upgrade")
            installed = _apt_packages(current.get("Install", ""), "install")
            removed = _apt_packages(current.get("Remove", ""), "remove")
            purged = _apt_packages(current.get("Purge", ""), "purge")
            reinstalled = _apt_packages(current.get("Reinstall", ""), "reinstall")
            downgraded = _apt_packages(current.get("Downgrade", ""), "downgrade")
            if upgraded:
                operation = "upgrade"
            elif installed:
                operation = "install"
            elif downgraded:
                operation = "downgrade"
            elif reinstalled:
                operation = "reinstall"
            elif purged:
                operation = "purge"
            else:
                operation = "remove"
            counter += 1
            transactions.append(
                HistoryTransaction(
                    id=f"apt-{counter}",
                    date=current.get("Start-Date", "Unknown date"),
                    requested_by=current.get("Requested-By", "APT"),
                    status="Failed" if "Error" in current else "Completed",
                    command=current.get("Commandline", f"apt {operation}"),
                    operation=operation,
                    altered_count=sum(
                        map(len, (upgraded, installed, removed, purged, reinstalled, downgraded))
                    ),
                    upgraded_pkgs=upgraded,
                    installed_pkgs=installed,
                    removed_pkgs=removed,
                    purged_pkgs=purged,
                    reinstalled_pkgs=reinstalled,
                    downgraded_pkgs=downgraded,
                )
            )
    return transactions


def load_transaction_history(limit: int = 200) -> list[HistoryTransaction]:
    """Load the newest available Nala and APT entries without failing the page."""
    transactions = [*_load_nala_history(), *_load_apt_history()]

    def date_key(transaction):
        try:
            return datetime.strptime(" ".join(transaction.date.split()[:2]), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.min

    transactions.sort(key=date_key, reverse=True)
    return transactions[:limit]

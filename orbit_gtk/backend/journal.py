"""Durable Orbit operation records; never edits shared APT or Nala history."""

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

DIRECTORY = Path("/var/lib/orbit-gtk/history")


class Journal:
    def __init__(self, action, directory=DIRECTORY):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        self.record = {
            "id": uuid.uuid4().hex,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "status": "Started",
            "pid": os.getpid(),
            "changes": [],
            "requested_by": os.environ.get("PKEXEC_UID", str(os.getuid())),
        }
        self.save()

    def save(self):
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".record-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o644)
            os.replace(name, self.directory / (self.record["id"] + ".json"))
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            Path(name).unlink(missing_ok=True)

    def event(self, kind, payload):
        if kind == "plan":
            self.record.update(
                changes=payload["changes"], download_only=payload.get("download_only", False)
            )
            self.save()
        elif kind in {"complete", "error", "cancelled"}:
            self.record["status"] = {
                "complete": "Completed",
                "error": "Failed",
                "cancelled": "Cancelled",
            }[kind]
            self.record["message"] = payload.get("message", "")
            self.save()


def read_records(directory=DIRECTORY):
    records = []
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text())
            if (
                isinstance(value, dict)
                and isinstance(value.get("changes"), list)
                and all(
                    isinstance(value.get(key), str) and value[key]
                    for key in ("id", "date", "action")
                )
                and isinstance(value.get("status", "Unknown"), str)
                and isinstance(value.get("requested_by", "Orbit"), str)
            ):
                if value.get("status") == "Started":
                    try:
                        pid = int(value.get("pid", -1))
                        if pid <= 0:
                            raise ProcessLookupError()
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        value["status"] = "Interrupted"
                    except PermissionError:
                        value["status"] = "Running"
                    else:
                        value["status"] = "Running"
                records.append(value)
        except (OSError, ValueError, TypeError):
            continue
    return records

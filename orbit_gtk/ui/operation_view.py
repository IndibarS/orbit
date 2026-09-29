"""Native transaction review and progress; GTK never waits on a child process."""

from __future__ import annotations

import json
import math
import subprocess
import threading
from collections.abc import Callable
from queue import Empty, Queue

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.ui.transaction_plan import TransactionPlan
from orbit_gtk.ui.widgets import expander_row


class OperationView(Gtk.Box):
    def __init__(
        self,
        title: str,
        command: list[str],
        *,
        on_done: Callable[[bool], None] | None = None,
        on_close: Callable[[], None] | None = None,
        on_event: Callable[[dict], None] | None = None,
        compact: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8 if compact else 16)
        self._compact = compact
        self._on_close = on_close
        self._on_event = on_event
        self._on_done = on_done
        self._finished = False
        self._cancelled = False
        self._has_percent = False
        self._error = ""
        self._complete = False
        self._notices = []
        self._warnings = []
        self._warning_count = 0
        self._proc = None
        self._events: Queue = Queue(maxsize=512)
        self._log_tail = ""
        self._log_dirty = False

        content = self
        for side in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{side}")(12 if compact else 20)
        heading = Gtk.Label(label=title, wrap=True, xalign=0 if compact else 0.5)
        heading.add_css_class("heading")
        self._heading = heading
        content.append(heading)
        self._icon = Gtk.Image(icon_name="emblem-synchronizing-symbolic", pixel_size=40)
        self._icon.set_visible(not compact)
        content.append(self._icon)
        self._phase = Gtk.Label(label="Authenticating…", wrap=True)
        self._phase.add_css_class("title-2")
        content.append(self._phase)
        self._status = Gtk.Label(
            label="Waiting for administrator authorization", wrap=True, selectable=True
        )
        content.append(self._status)
        self._progress = Gtk.ProgressBar(show_text=True)
        content.append(self._progress)
        self._transfer = Gtk.Label(wrap=True, visible=False)
        self._transfer.add_css_class("dim-label")
        content.append(self._transfer)

        self._review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self._summary = Gtk.Label(wrap=True, xalign=0)
        self._review.append(self._summary)
        self._changes = TransactionPlan()
        self._changes.set_visible(False)
        self._configuration_note = Gtk.Label(wrap=True, xalign=0)
        self._review.append(self._configuration_note)
        content.append(self._review)
        content.append(self._changes)

        self._details = expander_row(title="Technical details")
        self._buffer = Gtk.TextBuffer()
        log = Gtk.TextView(
            buffer=self._buffer,
            editable=False,
            cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        log.add_css_class("monospace")
        log_scroll = Gtk.ScrolledWindow(min_content_height=120, max_content_height=180)
        log_scroll.set_child(log)
        self._details.add_row(log_scroll)
        group = Adw.PreferencesGroup()
        group.add(self._details)
        content.append(group)

        footer = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        self.footer = footer
        self._cancel = Gtk.Button(label="Cancel", visible=False)
        self._cancel.connect("clicked", lambda _: self._respond(False))
        footer.append(self._cancel)
        self._apply = Gtk.Button(label="Apply changes", visible=False)
        self._apply.add_css_class("suggested-action")
        self._apply.connect("clicked", lambda _: self._respond(True))
        footer.append(self._apply)
        self._close = Gtk.Button(label="Running…", sensitive=False)
        self._close.connect("clicked", lambda _: self._on_close() if self._on_close else None)
        footer.append(self._close)
        content.append(footer)
        self._timer = GLib.timeout_add(50, self._drain)
        threading.Thread(
            target=self._run, args=(command,), name="orbit-operation", daemon=True
        ).start()

    def _run(self, command: list[str]) -> None:
        try:
            with subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            ) as process:
                self._proc = process

                def read_log():
                    # Bounded chunks handle output without newlines and keep memory bounded.
                    while chunk := process.stderr.read(4096):
                        self._events.put(("log", chunk))

                reader = threading.Thread(target=read_log, daemon=True)
                reader.start()
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict) or "event" not in event:
                            raise ValueError("Invalid event")
                        self._events.put(("event", event))
                    except (ValueError, TypeError):
                        self._events.put(("log", line))
                code = process.wait()
                reader.join()
                self._events.put(("exit", code))
        except OSError as error:
            self._events.put(("event", {"event": "error", "message": str(error)}))
            self._events.put(("exit", 1))

    def _drain(self) -> bool:
        for _ in range(100):
            try:
                kind, payload = self._events.get_nowait()
            except Empty:
                break
            if kind == "log":
                self._append(payload)
            elif kind == "event":
                try:
                    self._event(payload)
                except (KeyError, TypeError, ValueError, OverflowError) as error:
                    self._error = f"Invalid helper progress data: {error}"
                    self._append(self._error + "\n")
                    self._status.set_label(self._error)
                    if payload.get("event") == "plan":
                        self._respond(False)
            else:
                self._finish(payload)
        self._flush_log()
        self._changes.pulse()
        if not self._has_percent and not self._finished and not self._review.get_visible():
            self._progress.pulse()
        return not self._finished

    def _event(self, event: dict) -> None:
        kind = event["event"]
        message = str(event.get("message", ""))
        if kind in {"progress", "package-progress"} and event.get("package"):
            self._changes.update_progress(event)
        if kind == "log":
            self._append(message)
        elif kind == "progress":
            self._phase.set_label(event.get("phase", "Working…"))
            self._status.set_label(message)
            percent = event.get("percent")
            self._has_percent = isinstance(percent, (int, float)) and math.isfinite(percent)
            if self._has_percent:
                self._progress.set_fraction(max(0, min(1, percent / 100)))
                self._progress.set_text(f"{percent:.0f}%")
            else:
                self._progress.set_text("")
            if "bytes" in event:
                self._transfer.set_visible(True)
                self._transfer.set_label(
                    f"{AptManager.format_size(event['bytes'])} / "
                    f"{AptManager.format_size(event['total_bytes'])} · "
                    f"{AptManager.format_size(event['bytes_per_second'])}/s"
                )
            else:
                self._transfer.set_visible(False)
                self._transfer.set_label("")
        elif kind == "plan":
            self._show_plan(event)
        elif kind in {"error", "warning"}:
            self._append(message + "\n")
            if kind == "warning":
                self._warning_count += 1
                self._warnings = [*self._warnings[-7:], message]
            if kind == "error":
                self._error = message
                self._status.set_label(message)
        elif kind == "notice":
            self._notices.append(message)
        elif kind == "complete":
            self._complete = True
        elif kind == "cancelled":
            self._cancelled = True
        if self._on_event:
            self._on_event(event)

    def _show_plan(self, plan: dict) -> None:
        changes = plan["changes"]
        disk = plan["disk_bytes"]
        for value in (disk, plan["download_bytes"]):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Invalid transaction size")
        if plan["download_bytes"] < 0:
            raise ValueError("Invalid download size")
        self._changes.set_visible(True)
        self._changes.set_changes(changes)
        purging = any(change["action"] == "purge" for change in changes)
        removing = any(change["action"] in {"remove", "purge"} for change in changes)
        self._configuration_note.set_label(
            "Purging removes package-managed system configuration. Personal files are not removed."
            if purging
            else "Local configuration files are kept. Package service restarts may occur."
        )
        if plan.get("local_archive"):
            self._configuration_note.set_label(
                f"Local archive: {plan['local_archive']}\nOnly install files from a source you trust. "
                "This archive is not authenticated by repository signatures. Local configuration files are kept."
            )
        self._apply.remove_css_class("suggested-action" if removing else "destructive-action")
        self._apply.add_css_class("destructive-action" if removing else "suggested-action")
        self._phase.set_label("Review package changes")
        self._icon.set_visible(False)
        self._progress.set_visible(False)
        self._transfer.set_visible(False)
        self._status.set_label("Nothing will be installed or removed until you apply this plan.")
        if removing:
            self._status.set_label(
                "This plan removes packages. Review the Remove and Purge actions before applying."
            )
        self._progress.set_fraction(0)
        self._progress.set_text("Waiting for your review")
        self._summary.set_label(
            f"{len(changes):,} package changes · {AptManager.format_size(plan['download_bytes'])} download\n"
            f"{AptManager.format_size(abs(disk))} {'additional disk space' if disk >= 0 else 'disk space freed'}"
        )
        if plan.get("kept_back"):
            self._summary.set_label(
                self._summary.get_label() + f"\n{len(plan['kept_back'])} updates kept back by APT"
            )
        self._review.set_visible(True)
        self._cancel.set_visible(True)
        self._apply.set_visible(True)
        self._close.set_visible(False)
        self._changes._search.grab_focus()

    def _respond(self, apply: bool) -> None:
        try:
            self._proc.stdin.write("apply\n" if apply else "cancel\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # The process watcher reports the actual exit result.
        self._review.set_visible(False)
        self._changes.set_visible(apply and not self._compact)
        self._apply.set_visible(False)
        self._cancel.set_visible(False)
        self._close.set_visible(True)
        self._phase.set_label("Preparing transaction…" if apply else "Cancelling…")
        self._progress.set_visible(True)
        self._status.set_label("Please keep Orbit open while package changes are applied.")
        self._has_percent = False
        self._progress.set_text("")

    def _finish(self, code: int) -> None:
        self._finished = True
        success = code == 0 and self._complete and not self._error
        if not self._cancelled:
            self._changes.finish(success)
        self._apply.set_visible(False)
        self._cancel.set_visible(False)
        self._review.set_visible(False)
        self._close.set_visible(True)
        self._close.set_sensitive(True)
        self._close.set_label("Done" if success else "Close")
        self._progress.set_fraction(1 if success else 0)
        self._progress.set_visible(True)
        self._progress.set_text("Complete" if success else "Stopped")
        self._transfer.set_label("")
        if success:
            self._phase.set_label("Completed with warnings" if self._warning_count else "Completed")
            messages = ["Operation completed successfully.", *self._notices]
            if self._warning_count:
                messages.append(
                    f"{self._warning_count} warnings reported. Review the details below."
                )
                messages.extend(self._warnings)
                self._details.set_expanded(True)
            self._status.set_label("\n".join(messages))
            self._icon.set_from_icon_name("emblem-ok-symbolic")
        elif self._cancelled or code in (126, 127):
            self._phase.set_label("Cancelled")
            self._status.set_label(
                "No package changes were applied."
                if self._cancelled
                else "Administrator authorization was cancelled or unavailable."
            )
            self._icon.set_from_icon_name("dialog-information-symbolic")
        else:
            self._phase.set_label("Operation failed")
            self._status.set_label(
                self._error or f"The helper exited with status {code}. See technical details."
            )
            self._icon.set_from_icon_name("dialog-error-symbolic")
            self._details.set_expanded(True)
        color = (
            "warning"
            if success and self._warning_count
            else "success"
            if success
            else "warning"
            if self._cancelled or code in (126, 127)
            else "error"
        )
        for widget in (self._phase, self._icon):
            widget.add_css_class(color)
        if self._on_done:
            self._on_done(success)

    def _append(self, text: str) -> None:
        self._log_tail = (self._log_tail + text)[-65536:]
        self._log_dirty = True

    def _flush_log(self) -> None:
        if not self._log_dirty:
            return
        # Bound individual layout runs too: one enormous line can stall Pango.
        self._buffer.set_text(
            "\n".join(
                line[start : start + 512]
                for line in self._log_tail.split("\n")
                for start in range(0, max(1, len(line)), 512)
            )
        )
        self._log_dirty = False

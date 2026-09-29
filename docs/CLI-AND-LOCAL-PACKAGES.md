# CLI and local archive validation — 29 September 2026

Orbit now accepts a documented subset of Nala-style commands. They dispatch to
GUI pages and reviews through GApplication's single-instance command-line handler.
The local Nala references were `legacy/python/nala/nala.py`, `debfile.py` and
`cache.py`; Orbit uses python-apt directly and does not import Nala's terminal UI.

Local archives use `apt.debfile.DebPackage` to inspect conflicts and dependencies.
The archive is opened with the requesting user's credentials and copied into a
private directory before review, so changing the source during approval cannot
substitute a different archive. The dependency plan uses Orbit's existing held,
protected/essential-package and downgrade guards. Dependencies are authenticated
by APT. The GUI explicitly identifies the local archive as outside repository
signature authentication. Normal GUI approval is required.

Verified behavior:

- CLI help/version without a display; supported aliases, invalid/mixed requests,
  caller-relative filenames including spaces.
- Actual single-instance GApplication forwarding under an isolated D-Bus session.
  Installation dispatch is intercepted in this IPC test to avoid host changes.
- GUI navigation, local-install review routing and busy-operation rejection.
- Archive staging stability, private directory permissions, missing files,
  symlink/FIFO rejection.
- Real isolated APT/dpkg local install and reinstall, dependency install,
  cancellation, missing dependency download and retry, malformed archive,
  held-package and downgrade rejection, and per-package installation events.

The complete GTK regression suite passed (37 tests). Backend test discovery
passed 65 tests with 38 opt-in tests skipped. Local lifecycle and CLI IPC checks
run separately. No host package installation or repository edits were performed.

Limits: one local archive per request; no mixed local/repository batch or
unattended `--yes`. Maintainer-script failure after dependency installation can
leave partial changes; no rollback guarantee is made. Real desktop file-manager
association and Polkit-agent acceptance remain release checks.

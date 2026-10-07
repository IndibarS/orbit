# CLI commands and local packages

Updated 7 October 2026; the initial implementation was validated on 29 September.

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

## Entry points

Use **Browse → Install a downloaded package → Browse**, below the search bar,
or **Home → Quick Actions → Install a downloaded package**. The chooser accepts
one `.deb`. The header contains the refresh control, not another install button.
The installed desktop entry also registers `.deb` handling; real file-manager
acceptance remains to be checked.

From a checkout, prefix commands with `uv run`. Installed examples:

```sh
orbit-gtk update
orbit-gtk upgrade
orbit-gtk full-upgrade          # alias: dist-upgrade
orbit-gtk install gimp
orbit-gtk remove gimp
orbit-gtk purge gimp
orbit-gtk reinstall gimp
orbit-gtk autoremove
orbit-gtk clean
orbit-gtk fetch
orbit-gtk history
orbit-gtk search text editor
orbit-gtk show gimp             # alias: info
orbit-gtk list --installed bash
orbit-gtk list --upgradable
orbit-gtk install "./downloaded package.deb"
orbit-gtk install-local "./downloaded package.deb"
orbit-gtk "./downloaded package.deb"
```

`list` defaults to Installed; `--upgradable` opens Updates and does not accept a
search term. `clean` requests archive cleanup, retaining repository indexes.
`fetch` opens mirror benchmarking for the selected Debian, Ubuntu, Devuan, Mint or Kali
archive/suite; the repository is chosen in the GUI, not through additional
`fetch` arguments. See [mirror selection](MIRRORS.md). Full-upgrade permits
reviewed dependency removals; it does not change distribution releases or sources.

System changes still require administrator authentication and the applicable
review. `--help` and `--version` work without a display. Requests received during
an active operation are refused with a temporary message, not silently queued.

## Verified behavior

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

The September implementation passed 37 GTK tests and 65 backend tests at that
point. Current evidence is the [five-target Docker matrix](DISTRO-TESTING.md):
CLI IPC, real isolated local transactions, GTK checks, and built-package
installation/help all passed on Debian 13, Sid, Ubuntu, Mint and Kali. Each GTK
suite ran 43 checks with one metadata-dependent artwork skip. No host package
installation or repository edits were performed.

Limits: one local archive per request; no mixed local/repository batch or
unattended `--yes`. Maintainer-script failure after dependency installation can
leave partial changes; no rollback guarantee is made. Real desktop file-manager
association and Polkit-agent acceptance remain release checks.

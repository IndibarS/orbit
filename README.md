# Orbit GTK

Orbit is a native GTK4 / libadwaita package manager for APT-based systems.
It uses the distro's **python-apt** bindings for package data, dependency
resolution, downloads, installation, removal, and repository refreshes.
Nala's legacy Python code is a local reference, not a runtime dependency.

## Run with uv

Requirements: Python 3.11+, GTK **4.12+**, libadwaita **1.5+**, python-apt, PyGObject,
`pkexec`, and a desktop session with a Polkit authentication agent.
The Python interpreter must match the distro's compiled APT/GI bindings.

On a sufficiently recent Debian installation:

```sh
sudo apt install python3 python3-apt python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pkexec
./setup.sh
uv run orbit-gtk
```

`setup.sh` creates a uv environment using `/usr/bin/python3` and
`--system-site-packages`. It preserves an existing environment. It does not
install system packages or delete/recreate an existing environment. Set
`ORBIT_PYTHON` to select another compatible distro interpreter.

Do not run the whole GUI as root. Only a system-changing operation requests
administrator authentication. The helper imports Orbit's transaction code;
it does not launch `apt-get`, Nala, or a terminal emulator.

## Build a local Debian package

```sh
uv run python tools/build_deb.py
```

This builds `dist/orbit-gtk_0.2.0_all.deb` without installing it. The package
ships the application under `/usr/lib/orbit-gtk`, a desktop entry, and a dedicated
helper under `/usr/libexec/orbit-gtk`. The installed app uses a scoped Polkit
action requiring administrator authentication for each invocation. Both launchers
use isolated system Python and import only the installed application and system
libraries. The helper discards `APT_CONFIG` from its environment.

The development `uv run` launcher still elevates the current checkout through
generic Polkit authorization. Use it only with a trusted checkout. The Debian
artifact is an unsigned local build; interactive authentication and multiple
distribution releases still need acceptance testing before public release.

## Application icons

Browse, Installed, Updates and package details show application artwork from the
local AppStream catalogue. Debian distributes these separately from the APT
package index. Install `gir1.2-appstream-1.0` and `apt-config-icons` for catalogue
support, then refresh package lists in Orbit to populate repository metadata/icons.
These are optional: libraries, command-line packages, missing metadata and
unreadable artwork use a generic package icon. Orbit also uses installed theme
icons referenced by AppStream. It does not fetch remote icon URLs.
Catalogue indexing and image decoding run on workers; decoded artwork has a
bounded cache and rows keep the same icon dimensions while images load.

Package details also display screenshots supplied through AppStream, when present.
The gallery loads one image at a time over HTTPS when the details view opens,
with captions, previous/next navigation, loading feedback and retry on failure.
Downloads have size/time limits and a bounded in-memory cache; missing screenshot
metadata leaves the description free of empty placeholders.

## Weak or unavailable connections

Installed packages and searches use local APT data and remain available without
internet access when that data exists. A failed repository refresh is shown as a
failure, including partial refreshes; Orbit does not claim that all repositories
were checked. Retry refresh after connectivity returns.

APT downloads default to a 20-second inactive-connection timeout and two retries;
explicit system APT settings are preserved. Mirror lists, mirror probes and
screenshots use size caps and deadline checks between reads, so slow trickles and
truncated responses fail visibly. Screenshot failures keep package details usable
and offer Retry. Network work stays off GTK's main thread. These checks do not
promise an absolute time bound for the operating system's DNS resolver.

## GUI behavior

- **Updates:** review the exact dependency plan inline, then follow each
  package's download/unpacking/configuration status in its own row. Download
  bars use actual byte counts. Installation bars pulse because APT exposes
  an overall installation percentage, not a per-package installation percentage.
  The overall bar uses APT's reported progress. Completed download and installation
  stages hide their bars. Upgrade buttons disappear during the operation.
  Successful operations and cancellations return to the page with a temporary
  message, without a Done/Close step. Failures and completion warnings remain
  visible for inspection. Per-package bars stay hidden before work begins.
  Package details offers one Remove action with an optional, unchecked
  **Purge configuration files** checkbox; either choice opens the dependency review.
  Transaction reviews show colored action counts and searchable package/version
  rows. Filtering is for inspection: Apply always uses the complete reviewed
  plan. Removals receive a prominent warning and destructive Apply styling.
- **Browse / Installed:** search real package data, inspect package details,
  install or remove after reviewing all dependency changes. Installed uses recycled
  rows so scrolling does not retain a widget for every package; no “Show more”
  button is needed. Ctrl+F opens search or focuses the installed
  filter. Package details include install/upgrade review, remove and explicit
  purge actions; purge deletes package-managed system configuration after review.
  Reinstall downloads the installed version again to restore package files,
  preserving local configuration; unavailable old versions are rejected.
- **History:** inspect recorded Nala and APT transactions, including package versions.
  Colored count chips and expandable action groups distinguish installs, upgrades,
  removals, purges, reinstalls and downgrades. Package names and version changes
  appear as individual rows; long groups scroll without creating every row at once.
  Commands and requested-user details sit in a separate disclosure.
- **Cleanup:** remove downloaded archives or explicitly selected repository
  indexes under APT's corresponding locks. Review unused dependencies before
  removing them through APT; configuration files are retained. Failed space
  calculations show an error and Retry rather than claiming the cache is clean.
- **Mirrors:** benchmark Debian archive Release files and manage an optional
  Orbit-owned source file, following Nala fetch: skip mirrors already enabled
  for the release and save without refreshing indexes. Saving finishes inline.
  Existing sources remain active; refresh package lists separately when ready.
  The next refresh can download indexes from each new mirror. The benchmark
  measures a Release-file transfer, not sustained package-download bandwidth.
- **Home:** real package/system statistics, upgrade navigation, cache cleanup,
  and repair of interrupted dpkg configuration.
  Statistics report failures independently, with Retry; cached upgrade counts
  do not claim that remote repositories have just been checked.

Operations retain warnings visibly at completion, and inline reviews scroll in
short windows. The [adversarial audit](docs/AUDIT-2026-09-24.md) records remaining
feature gaps and release limits.

Package operations preserve holds, reject essential/protected removals and
unrequested downgrades, and require authenticated downloads. Normal upgrades
never remove installed packages. APT's held-back updates are reported explicitly.
Local configuration files are kept; package service restarts can occur.
Cancellation is available during transaction review. Once dpkg is applying
changes, Orbit prevents closing the operation/window through its normal UI.
An abandoned review expires after five minutes without applying changes.

## Structure

```text
orbit_gtk/
  main.py                  application entry point
  backend/
    apt_cache.py           locked, cached read-only package snapshots
    apt_manager.py         GUI-facing backend facade
    transactions.py        resolve, validate, approve, commit through python-apt
    progress.py            APT callbacks → structured progress events
    helper.py              privilege boundary, cleanup, mirror changes, recovery
    history.py             Nala/APT history readers
    mirrors.py             Debian sources and mirror discovery
    mirror_benchmark.py    bounded concurrent Release-file measurements
    models.py              typed snapshots without fabricated metrics
  ui/
    window.py              navigation and one-operation-at-a-time coordination
    operation_view.py      shared review/progress component and process transport
    operation_dialog.py    dialog host for non-inline operations
    package_dialog.py      real package metadata
    widgets.py             plain-text row factories
    pages/                 home, updates, browse, installed, history, cleanup, mirrors
```

The helper's stdout is newline-delimited JSON; approval travels over stdin.
Native dpkg output is captured separately and shown only in expandable technical
information. GTK never blocks waiting for the helper. The only explicit package
executable is `dpkg --configure -a` for recovery, which has no Python configuration
API. APT itself invokes dpkg internally for ordinary transactions.

## Verify

```sh
uv run python -m unittest discover -s tests -p test_backend.py -v
GDK_BACKEND=x11 GSK_RENDERER=cairo GSETTINGS_BACKEND=memory ORBIT_GUI_TESTS=1 \
  xvfb-run -a uv run python -m unittest discover -s tests -p test_gui.py -v
uv run python tests/isolated_transaction.py
uvx ruff check orbit_gtk tests
uvx ruff format --check orbit_gtk tests
uv build
```

GUI tests require `xvfb` (optional screenshots also require ImageMagick).
The real APT/dpkg lifecycle test requires `bubblewrap` and `dpkg-dev`. It creates
an isolated user namespace, mounts the host filesystem read-only, creates a
local repository and temporary package database, then verifies installation,
upgrade, removal, approval/cancellation, and helper event framing. **It must not
be run with sudo.** Test fixture packages are confined to tests; the app never
substitutes example data for missing system data.

See [the validation report](docs/QUALITY.md) for the assessment and release limits,
and [Nala reference notes](docs/NALA_REFERENCE.md) for the adaptation rationale.

### Command-line entry into the GUI

Run `uv run orbit-gtk <command>` from the checkout, or `orbit-gtk <command>`
after installing the Debian package. Commands open the existing Orbit window
when it is already running. System changes still require the GUI review and
administrator authentication; unattended `-y` / `--yes` is intentionally unsupported.

```sh
orbit-gtk update
orbit-gtk upgrade
orbit-gtk full-upgrade          # dist-upgrade is an alias
orbit-gtk install gimp
orbit-gtk remove gimp
orbit-gtk purge gimp
orbit-gtk reinstall gimp
orbit-gtk autoremove
orbit-gtk search text editor
orbit-gtk show gimp             # info is an alias
orbit-gtk list --installed
orbit-gtk list --upgradable
orbit-gtk history
orbit-gtk clean                 # review archive cleanup; keeps repository lists
orbit-gtk fetch                 # benchmark mirrors; select/save them in the GUI
orbit-gtk install ./downloaded-package.deb
```

`list` defaults to Installed; an optional search term filters that list.
`--help` and `--version` work without a graphical display. This is a supported
subset of Nala-style commands, not a drop-in Nala command-line replacement.
Commands received while a package operation is active are refused with a toast;
they are not silently queued for later execution.

### Local Debian packages

Use **Browse → Install a downloaded package → Browse**, the matching
**Home → Quick Actions** entry, or the `install` command
above, or **Open With → Orbit** on a `.deb` after installing the desktop entry.
`orbit-gtk install-local /path/to/package.deb` and `orbit-gtk /path/to/package.deb`
are also supported. Relative paths resolve from the calling terminal's directory.

Orbit accepts one regular archive at a time. It stages a private copy, checks
architecture/conflicts/dependencies using `apt.debfile`, and reviews the package
and repository dependency changes together. Repository dependencies retain APT
signature checks; a local archive itself is not authenticated by repository
signatures. Missing dependencies may require internet access. Download failure
prevents the local install and is shown in the GUI. A later maintainer-script
failure can leave dependencies installed or the package partially configured;
Home's package-health warning helps identify that state. Holds and downgrades
are rejected. Mixed local/repository requests and batches of local archives
are not supported yet.

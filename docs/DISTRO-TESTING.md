# Distribution compatibility tests

The Docker matrix exercises the distro's own Python, python-apt, GTK and
libadwaita packages, rather than substituting bindings from the development
machine. Orbit requires Python 3.11+, GTK 4.12+ and libadwaita 1.5+; older
releases such as Debian 12 and Ubuntu 22.04 are outside this matrix.

## Verified results

Completed 2026-10-07 and rerun on all five targets after adding distro-specific
mirror discovery. These are the runtime versions actually observed:

| Distribution | Python | GTK | libadwaita | Stages |
| --- | --- | --- | --- | --- |
| Debian 13 (trixie) | 3.13.5 | 4.18 | 1.7 | 8/8 passed |
| Debian Sid | 3.14.8 | 4.24 | 1.10 | 8/8 passed |
| Ubuntu 24.04 | 3.12.3 | 4.14 | 1.5 | 8/8 passed |
| Linux Mint 22.3 | 3.12.3 | 4.14 | 1.5 | 8/8 passed |
| Kali Rolling | 3.14.7 | 4.22 | 1.9 | 8/8 passed |

All five GUI suites ran 43 checks, with one artwork check skipped because GIMP's
AppStream artwork was absent. Each target passed six explicit network tests
and the isolated transaction scenarios. All five include the search optimization,
`.pgp` identity support and the mirror provider/selector feature. Final focused
reruns on rebuilt images verified the configured archive identities, APT parsing
of generated source stanzas, 12 mirror-provider tests and 3 GTK mirror tests.
Those focused checks ran without network access after the final source-badge fix.
Kali's image reports `VERSION_ID=2025.3` while using the rolling repository
runtime above; this is a container snapshot, not a claim about a fresh installer.

Lint/format checks and the local Debian package build also passed. The test
runner now discards old results before a rerun so interruption cannot appear
as a current pass. Detailed logs remain in `.artifacts/docker/`.

## Run the matrix

From the checkout, with Docker installed and accessible:

```sh
python3 tools/test_distros.py
# Or select targets and concurrency:
python3 tools/test_distros.py debian13 ubuntu2404 --jobs 2
```

Targets are Debian 13 (`debian:trixie`), Debian Sid (`debian:sid`), Ubuntu
24.04 (`ubuntu:24.04`), Linux Mint 22.3
(`linuxmintd/mint22.3-amd64:latest`) and Kali Rolling
(`kalilinux/kali-rolling:latest`). The Mint image is published by linuxmintd;
it is not a fresh desktop installation from the Mint installer. The fixture
installs Mint's `base-files/zena` because the build image otherwise retains
Ubuntu's identity. It prioritizes distro executables over `/usr/local/bin` to
avoid the image's `dpkg-deb` wrapper, which mishandles spaces in filenames.
Every target verifies its actual `/etc/os-release` ID before testing.

The runner builds `orbit-distro-test:<target>` images, then removes each
container after testing. Per-target locks prevent concurrent runs from replacing
each other's logs. Images remain available for cached reruns. It does not
mount the checkout, host APT directories, or Docker socket into containers.
Real transaction tests use a temporary APT/dpkg root inside the container;
installation of the built Orbit package changes only the disposable container.

The runner allows two concurrent targets by default, bounds each image build to
30 minutes and each container test run to 15 minutes. A failed build/test or
timeout returns a nonzero exit status. A rerun clears the previous result and
test log before building; a missing result after interruption is not a pass.

Builds need internet access. Test fixtures use local package archives and a
loopback HTTP server, including slow, failed, and interrupted downloads.
Logs and exit statuses are written to `.artifacts/docker/<target>/`, excluded
from Git, alongside coverage artifacts (`.coverage`, `.coverage.*` and `htmlcov/`). The source
harness remains tracked under `tools/` and `tests/docker/`. `.dockerignore` restricts the build context to application and test
inputs, excluding development environments and reference repositories.

## Coverage and limits

Each target runs backend unit tests, GTK integration checks under Xvfb, GUI
command-line forwarding, network-failure tests, real isolated package
transactions, `.deb` building, installation of that `.deb`, and its installed
command's help output. The runner fails for a failed stage, missing archive,
or timeout. GUI/network tests skipped by the generic discovery stage are run
explicitly in their own stages.

This checks distribution API compatibility and package operations, not complete
desktop certification. Real graphical Polkit authorization, Wayland, screen
readers, distribution installer defaults, repository outages, and every
possible dependency graph still need desktop/VM testing. Artwork tests may
skip when repository AppStream metadata is absent. Distro-specific mirror
discovery now supports Debian, Ubuntu, Mint and Kali;
see [MIRRORS.md](MIRRORS.md) for independent archive/suite selection and limits.
LMDE is covered by source fixtures, not an additional distro image.

## Issues found and fixed

- Current Debian images use a `.pgp` archive keyring. Orbit's suite detection
  recognized only `.gpg` and `.asc`, so Sid was labelled with the ambiguous OS
  codename `forky`. Detection now recognizes `.pgp` too, with a regression test
  that still excludes vendor keys. The harness also checks the real displayed
  suite against configured Debian sources and preserves derivative identities.
- Search could exceed 30 seconds on Debian's compressed APT indexes because
  alphabetical traversal repeatedly sought backwards through compressed files.
  Orbit now reads description records in file order and preserves the same
  result ranking. A profiled original scan took 149 seconds; the revised scan
  returned in roughly 3 seconds in this container environment. These are
  diagnostic measurements, not a universal performance guarantee.
- Ubuntu's libadwaita 1.5 warned that Orbit's adaptive window lacked a minimum
  size. The window now declares one.
- The initial derivative limitation was shown as a generic read error. After
  fixing that state, separate Ubuntu, Mint and Kali mirror providers were added;
  unknown distributions retain an explanatory unsupported page.
- The container fixtures needed Hatchling's separate `editables` package, Mint's
  actual base identity package, and an unmodified distro `dpkg-deb` executable.
  Those test-environment problems are corrected rather than counted as passes.

For the overall assessment and remaining desktop/feature work, see
[QUALITY.md](QUALITY.md) and [FEATURE-COVERAGE.md](FEATURE-COVERAGE.md).

For focused mirror regressions after setup:

```sh
uv run python -m unittest discover -s tests -p test_mirror_providers.py -v
GDK_BACKEND=x11 GSK_RENDERER=cairo GSETTINGS_BACKEND=memory ORBIT_GUI_TESTS=1 \
  dbus-run-session -- xvfb-run -a uv run python -m unittest discover -s tests -p test_gui.py -k mirror -v
```

These fixture tests do not contact live catalogues or modify system source files.
The full Docker command above also runs them as part of its regular suites.

### Archive metadata detection follow-up

Discovery no longer requires a matching OS ID. Offline fixture tests cover an
unknown derivative with an Ubuntu base, mixed Kali/Debian archives, absent
os-release, missing/unreadable/ambiguous metadata, and vendor-key exclusion.
These fixtures do not establish full desktop support for additional distros.

### Devuan catalogue coverage

Devuan mirror discovery supports merged and Devuan-only archives. Focused tests
cover catalogue records without blank separators, inactive/protocol filtering,
source-layout isolation and wrong-Origin/Label rejection. Live smoke checks
parsed 34 active HTTP(S) candidates and validated one mirror in both layouts.
Devuan is not yet a target in the full container matrix above.

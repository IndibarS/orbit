# Nala reference notes

Updated 7 October 2026.

Reference checkout: `nala/legacy/python/nala`, at repository commit
`4c10370a4bbeda5912a8dd5ac2e6eead8e4a961c` during this refactor.

Nala's legacy modules import global CLI arguments, terminal state, Rich rendering,
and process-exit paths. Importing those modules into a GTK application would tie
application startup and package operations to Nala's CLI lifecycle. Orbit instead
uses the same python-apt interfaces and adapts the relevant design patterns:

| Reference | Orbit implementation |
| --- | --- |
| `cache.py`, `Cache.commit_pkgs` | `transactions.py`: system locking and dependency planning, then the public `apt.Cache.commit` implementation, which already owns archive locking, fetching and dpkg dispatch |
| `dpkg.py`, progress classes | `progress.py`: python-apt `OpProgress`, `AcquireProgress` and `InstallProgress` callbacks become structured GUI events |
| `search.py`, `set_search_origin` | `apt_cache.py`: read cached APT `PackageFile` labels instead of constructing trust-checking Origin objects for every displayed package |
| `cache.py`, virtual-package filtering | filter inaccessible `$` packages and records without real versions |
| `fetch.py`, `build_sources` / `write_sources` | `mirrors.py` and `mirror_catalogues.py`: distro-specific discovery and compatible Release measurements; save independent archive/suite stanzas, skip existing URI/suite entries, and do not run APT refresh as part of saving |
| `debfile.py`, `cache.py` | `local_deb.py`: private archive staging, `apt.debfile` dependency checks and reviewed local installation |
| `nala.py`, argument routing | `cli.py` and `main.py`: supported commands route into GUI pages/reviews through GApplication |
| `history.py` | parse current and older upgrade record layouts, plus automatically removed packages, without importing CLI state |

No terminal-rendering code is vendored. Nala's reference checkout remains
unchanged and is excluded from Orbit's builds. Its GPL notices remain intact.
Any future literal copying of Nala implementation code must preserve its
copyright/license obligations; this refactor is an independent implementation
around the distro-provided APT APIs.

Orbit supports normal and full upgrades, install/remove/purge/reinstall,
repository refresh, Debian/Ubuntu/Devuan/Mint/Kali mirror benchmarking, cleanup, history inspection and
single local `.deb` installation. Nala-style commands navigate to GUI workflows;
they do not bypass review or authorization. See [CLI and local packages](CLI-AND-LOCAL-PACKAGES.md).

Orbit does not claim complete CLI feature parity: history replay/undo, release
migration, hold editing, arbitrary version selection and interactive debconf
forms remain absent. APT full-upgrade resolves dependency transitions within
configured repositories; it is not a distribution-release migration wizard.

Mirror saving writes Orbit's dedicated source file and leaves existing system or
Nala sources untouched. It does not automatically refresh indexes. Separate
providers discover Debian, Ubuntu, Devuan, Mint and Kali archives, including on other
derivatives through configured sources and cached APT Origin metadata. Mint and its base
distribution are selected independently; see [mirror support](MIRRORS.md).

The [distro tests](DISTRO-TESTING.md) also exposed slow random access through
compressed APT indexes. Orbit now visits translated description records in file
order before ranking search results. This is an Orbit compatibility optimization,
not a claim that Nala code is imported or that its implementation was copied.

# Nala reference notes

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
| `fetch.py`, `build_sources` / `write_sources` | Debian discovery and measurements; save a dedicated source file, skip existing URI/suite entries, and do not run APT refresh as part of saving |
| `history.py` | parse current and older upgrade record layouts, plus automatically removed packages, without importing CLI state |

No terminal-rendering code is vendored. Nala's reference checkout remains
unchanged and is excluded from Orbit's builds. Its GPL notices remain intact.
Any future literal copying of Nala implementation code must preserve its
copyright/license obligations; this refactor is an independent implementation
around the distro-provided APT APIs.

Compared with Nala, Orbit currently emphasizes normal upgrades, install/remove,
refresh, source inspection/benchmarking, cleanup, and recorded history. It does
not claim complete CLI feature parity: history replay/undo, distribution upgrades,
local .deb installation, hold editing, and interactive debconf forms are not yet
exposed. Those controls are absent rather than nonfunctional placeholders.

# Validation and engineering assessment

Updated 7 October 2026. The current evidence is the completed
[five-target Docker matrix](DISTRO-TESTING.md), covering Debian 13, Debian Sid,
Ubuntu 24.04, Linux Mint 22.3 and Kali Rolling. All eight stages passed on every
target. Exact runtime versions, image qualifications and reproduction instructions
are recorded in that report.

The last scored adversarial audit was **9.0/10**, recorded in the
[September audit](AUDIT-2026-09-24.md). That is a historical engineering judgment,
not a newly measured score. The new tests resolve several earlier gaps but do
not justify calling the app flawless or claiming feature parity with every other
package manager. The [feature backlog](FEATURE-COVERAGE.md) distinguishes
implemented workflows from remaining acceptance work.

## Current validation

- Backend tests cover dependency guards, holds, kept-back packages, progress,
  history, source validation, system identity, metadata and failure handling.
- Each distro ran 43 GTK checks under Xvfb: 42 passed and one real-artwork check
  skipped because GIMP's AppStream artwork was absent. Missing artwork is not
  counted as a verified catalogue integration.
- Single-instance command forwarding was tested in isolated D-Bus sessions.
  System-changing dispatch is intercepted in that IPC test; actual package
  changes are tested separately in temporary APT/dpkg roots.
- Six explicit network tests passed per target. Coverage includes bounded reads,
  stalled/trickling/truncated responses and recovery. Isolated APT tests also
  verify failed refresh preserves cached data, unavailable archives do not change
  package files, and a later retry succeeds.
- Real isolated transactions cover install, normal/full upgrade, remove, purge,
  reinstall, autoremove, local archives, review cancellation, dependency guards,
  package-row event identity, and GUI pipe loss before/after approval.
- The mirror follow-up passed all eight distro stages, then final offline focused
  checks on rebuilt images: real archive detection, APT source parsing, 12 provider
  tests and 3 GTK mirror tests. Live official-catalogue and sampled compatible
  Release checks succeeded for all four providers; this does not certify every
  listed mirror's availability.
- Debian package build, installation into disposable containers and the installed
  command's help output passed on every target. The local `.deb` was rebuilt.
- Ruff lint/format and diff whitespace checks passed during this work. The
  documented test runner uses the distro's Python/APT/GI stack through uv.

Generic discovery skips opt-in tests, which run explicitly in later stages.
Counts from September describe older suites and should not be combined with
current counts. No host package database or APT sources were modified by the
matrix; test containers were removed, and their images remain cached.

## Fixes verified by the distro tests

- **Compressed-index search:** alphabetical traversal caused repeated backward
  seeks through APT's compressed package indexes. Description records now load
  in file order, followed by the same result ranking. Cancellation and description
  matching retain regression coverage.
- **Debian identity:** `.pgp` archive keyrings are recognized alongside `.gpg`
  and `.asc`. Real Sid and trixie images now display the configured Debian suite;
  derivatives retain their own OS identity. Vendor-key exclusions remain tested.
- **Derivative mirrors:** the initial explanatory unsupported state has been
  superseded by separate Ubuntu, Mint and Kali providers. Archive/suite selection,
  Release compatibility checks and scoped saves preserve other repositories.
  Unknown distributions still receive an explanatory unsupported state. System
  badges are restricted to the recognized archive rather than every feed using
  the same suite name, so PPAs are not labelled as Ubuntu archive mirrors.
- **Older libadwaita:** the adaptive window declares a minimum size, resolving
  the warning exposed by Ubuntu's libadwaita 1.5.
- **Reliable fixtures:** Mint uses its actual base identity package and distro
  `dpkg-deb`, rather than the build image's broken wrapper. Runner locks, bounded
  execution, container cleanup and removal of stale results make reruns auditable.

## UI and functionality now implemented

Installed packages use recycled GTK rows, not append-only pagination. Search
clears stale actions immediately, reports its 200-result ceiling and balances
Install/Installed controls. History uses semantic action chips and expandable,
recycled package rows; color accompanies text rather than replacing it.

Updates and shared transaction reviews retain package-row download, unpacking
and configuration status. Download fractions use byte counts; installation bars
pulse because APT supplies overall, not per-package, installation percentages.
Bars hide when a stage completes and before work begins. Upgrade controls hide
during execution. Successful operations and cancellations return with temporary
messages; failures and completion warnings remain inspectable.

Package details offer one Remove action with an unchecked purge-configuration
option, plus appropriate install/upgrade/reinstall actions. Local `.deb` files
have a Browse-page chooser, Home shortcut, command-line entry and installed file
association. See [CLI and local packages](CLI-AND-LOCAL-PACKAGES.md) for limits.

Icons come from local AppStream/theme metadata with a generic fallback; remote
icon URLs are not fetched. Optional screenshots use bounded HTTPS transfers,
asynchronous decoding and retry. Mirror saving follows Nala's save-only pattern:
Orbit owns a separate source file and preserves existing sources. Each archive
and suite has an independent stanza; see [provider support](MIRRORS.md).

## Measured performance

The distro investigation profiled an original Debian `bash` search at roughly
149 seconds, dominated by APT record lookup. A revised scan returned in roughly
3 seconds. Profiling overhead and cache/machine variation mean these diagnostic
runs are not a controlled benchmark or a universal speedup claim.

Historical measurements remain useful context: the September installed-snapshot
origin-label fix reduced one run from 82.48 seconds to 0.415 seconds. Later tests
profiled 10,000 installed fixtures with approximately 205–206 live row containers.
See the dated audit for conditions; these measurements are not current guarantees
for every system, catalogue or artwork workload.

## Remaining release limits

- **Desktop acceptance:** real graphical Polkit authorization, file-manager
  associations, Wayland, screen readers, keyboard-only use, HiDPI and hardware
  rendering still need acceptance testing. Xvfb success does not establish these.
- **Release distribution:** the local package already installs a root-owned
  helper and scoped Polkit action. Public release metadata, licensing review and
  distribution remain work. The development launcher elevates a trusted checkout
  through generic Polkit authorization; it is not the installed privilege boundary.
- **Package behavior:** trusted fixture repositories do not establish repository
  signature infrastructure, arbitrary maintainer-script behavior, service restarts,
  general debconf interaction or every dependency graph. Configuration is kept
  by default; an explicit purge removes package-managed configuration.
- **Recovery:** cancellation and GUI pipe loss are tested. Power loss, forced
  helper/dpkg termination, restart reconciliation and rollback remain unproven.
- **Network/platform breadth:** proxy/captive-portal behavior, all repository
  outages, OS DNS timing and older distribution stacks are not fully covered.
  Mirror discovery covers Debian, Ubuntu, Devuan, Mint and Kali; ARM/ports and arbitrary
  derivatives still need acceptance coverage. Required versions are Python 3.11+,
  GTK 4.12+ and libadwaita 1.5+.
- **Feature gaps:** hold editing, arbitrary version selection, history replay,
  batch local archives, conffile/debconf choices, richer discovery, other package
  backends and offline-update orchestration remain in the acceptance backlog.

No score of 10 or complete desktop certification is claimed.

Devuan catalogue follow-up: both merged and Devuan-only archive layouts passed
focused parser/discovery/isolation tests and sampled live Release validation.
The existing five-target distro matrix does not include Devuan.

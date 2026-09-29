# Feature coverage and acceptance backlog

Reviewed 24 September 2026. This is a concrete backlog, not a claim that Orbit
already surpasses every package manager. Feature presence alone is insufficient:
each new workflow needs dependency review, clear progress and recovery tests.

## Reference products

- Nala: the local legacy Python checkout described in [NALA_REFERENCE.md](NALA_REFERENCE.md)
  remains the APT workflow reference. Its history replay and advanced package
  workflows are not all exposed in Orbit.
- [KDE Discover](https://apps.kde.org/discover/) documents category browsing,
  screenshots/reviews and multiple software sources (distribution repositories,
  Flatpak, Snap and AppImages). Orbit currently manages APT packages only.
- [GNOME Software](https://apps.gnome.org/Software/) documents application
  discovery, categories, screenshots, extensions and offline system updates.
  Orbit has metadata artwork/screenshots but no app categories or offline updater.

## Current coverage

| Workflow | Orbit status | Next acceptance requirement |
| --- | --- | --- |
| APT install, upgrade, remove, purge, reinstall | Implemented and isolated dpkg tested | Wider real-package maintainer-script matrix |
| Dependency review | Complete plan, action counts, name/action filters, recycled rows | Screen-reader and keyboard-only acceptance |
| Per-package progress | Inline download, unpacking and configuration status | Recovery after abrupt GUI termination |
| Unused dependency removal | Reviewed and isolated dpkg tested | Broader dependency graph fixtures |
| Weak/offline connections | APT retry/timeout defaults, explicit failures, cached browsing, capped metadata transfers; fault tests | Wider proxy, DNS, captive-portal and intermittent-connectivity acceptance |
| Mirror discovery and source ownership | Implemented, existing sources preserved | Cross-release source layouts |
| History | APT/Nala parsing, action chips and lazy grouped rows | Replay/undo with availability and reversal checks |
| Application icons/screenshots | AppStream metadata, optional fallback | More distro catalogues and image formats |
| Package holds and version selection | Holds displayed/respected; editing absent | Reviewed state changes and version/hold persistence tests |
| Local .deb files | Single-archive chooser, CLI/file association, staged copy, dependency review and isolated lifecycle tests | Multi-archive dependency resolution and broader maintainer-script testing |
| Full upgrades | Implemented with inline removal review and isolated dependency-transition tests | Wider real-package transition matrix |
| Batch selections | Absent | Persistent pending plan, undo selection, stale-cache reconciliation |
| Package changelogs/dependency inspection | Absent | Accessible detail tabs, cancellation, size/time limits |
| Installed catalogue scaling | Recycled GTK rows, profiled with 10,000 packages | Broader hardware and artwork-heavy memory/latency measurements |
| Offline updates and restart recovery | Absent | Durable transaction/service lifecycle and recovery protocol |
| Configuration choices | Keeps local conffiles | Diff/choice UI and debconf integration without blocking GTK |
| Flatpak/Snap/other stores | Absent | Separate native backend contracts, source identity and permissions |
| Release packaging | Local .deb, root-owned paths, scoped policy | Real Polkit-agent acceptance, distro matrix and release metadata |

## Order of work

1. Finish package safety and recovery acceptance before broadening privileged operations.
2. Add reviewed holds/version selection, multi-archive installation and batch selection.
3. Complete catalogue scaling and accessible keyboard/screen-reader interaction.
4. Add richer discovery and additional backends with visible source identity.

Do not add inactive controls for unimplemented features. Keep application discovery,
package maintenance and transaction review distinct and clearly named. Color must
always accompany text labels, never replace them.

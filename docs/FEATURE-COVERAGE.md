# Feature coverage and acceptance backlog

Reviewed 7 October 2026 after the five-target Docker matrix. This is a concrete backlog, not a claim that Orbit
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
| Mirror discovery and source ownership | Debian-only catalogue; Orbit-owned file; existing sources preserved; clear derivative limitation | More source layouts and live mirror acceptance |
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
| Distribution identity | Sid/trixie derived from enabled Debian sources, including `.pgp` keys; derivatives retain OS identity | More mixed/vendor source layouts |
| Distro compatibility | Eight stages passed on Debian 13, Sid, Ubuntu 24.04, Mint 22.3 and Kali Rolling | Real desktop/Wayland/Polkit acceptance and older-release requirements |
| Search | Ranked names/descriptions; compressed indexes read in record order; 200-result ceiling | More hardware, catalogue and memory measurements |
| Release packaging | Local .deb, root-owned paths, scoped policy; build/install/CLI passed across five Docker targets | Real Polkit-agent acceptance, release metadata and licensing review |

## Order of work

1. Finish package safety and recovery acceptance before broadening privileged operations.
2. Add reviewed holds/version selection, multi-archive installation and batch selection.
3. Complete catalogue scaling and accessible keyboard/screen-reader interaction.
4. Add richer discovery and additional backends with visible source identity.

Do not add inactive controls for unimplemented features. Keep application discovery,
package maintenance and transaction review distinct and clearly named. Color must
always accompany text labels, never replace them.

See [DISTRO-TESTING.md](DISTRO-TESTING.md) for exact runtime versions, image
qualifications and skipped artwork coverage. Passing container checks does not
complete the desktop acceptance backlog or establish feature parity.

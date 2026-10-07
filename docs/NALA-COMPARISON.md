# Orbit versus Nala: implementation audit

Reviewed 7 October 2026. This is a source/help audit, not a head-to-head speed or
reliability benchmark. No system packages or APT sources were modified.

## Comparison baseline

- Installed Nala 0.16.0: command help for install, upgrade, search, list, show,
  clean, history, fetch and autoremove.
- Local reference commit `4c10370a4bbeda5912a8dd5ac2e6eead8e4a961c`:
  `nala/legacy/python/nala` for the Python implementation. Rust-specific findings
  are explicitly identified below; they are not claimed as installed 0.16.0 features.
- Current Orbit working tree, including metadata-driven discovery and Devuan.

Orbit covers common package maintenance and offers a graphical experience that
Nala's terminal interface does not. Nala still exposes substantially more APT
workflows and controls. Catalogue-family parity does not mean fetch-option parity,
and passing Orbit's tests does not establish superiority over Nala.

## Package operations and transaction control

| Capability | Nala reference | Orbit today | Priority/action |
| --- | --- | --- | --- |
| Install/remove/purge/reinstall | Package transactions, including advanced selectors | Exact package names; reviewed plans; dedicated reinstall | Core present; broaden supported selectors carefully |
| Normal/full upgrade | Both modes; upgrade refreshes lists by default; optional autoremove | Both modes; refresh and autoremove are separate operations | P2: optional refresh-before-upgrade and combined cleanup review |
| Version and release selection | `pkg=version`, `--target-release`; explicit downgrade handling | Package-name validator rejects version/release syntax; downgrades rejected | P1: versions/source selector with downgrade-specific review |
| Upgrade exclusions | Repeated `--exclude`, including globs | Upgrade one or all; no arbitrary exclusion set | P1: select/deselect upgrades and explain dependency-induced changes |
| Batch selection | Multiple package arguments and glob expansion | Multiple exact repository names via CLI; no persistent GUI selection basket | P1: collect a mixed action plan in GUI; CLI batching already exists |
| Download only | `--download-only` | No download-only workflow | P2: reviewed prefetch without invoking dpkg |
| Recommends/suggests | Per-operation switches | Inherits APT configuration; no UI override | P2: explicit advanced review options |
| Broken dependency repair | `install --fix-broken`, including no-package invocation | Detects broken dependencies; repair runs `dpkg --configure -a` only | P1: separate dependency-resolution repair with a complete plan |
| Configuration decisions | Interactive terminal support and dpkg option overrides | Noninteractive debconf; keeps old conffiles; automatic needrestart mode | P1: explain defaults, add conffile diff/choice and supported configuration interaction |
| Virtual packages | Provider filtering/resolution | Real-package-only search; no provider picker | P2: expose providers and resolve explicit choices |
| Local archives | Local-package handling with multi-package preparation | One staged local .deb; rejects batches and mixed local/repository requests | P1: resolve local batches together and show all dependency effects |
| Remote .deb URLs | Direct URL installation supported | CLI treats URL-like arguments as local paths; no downloader workflow | P2: explicit bounded download and review, or clear unsupported-URL error |
| Essential removal/trust bypass | Advanced override switches | Refuses essential/protected removal and unauthenticated commit | Keep conservative defaults; not a defect merely because Nala permits an override |

The residual-configuration case deserves special attention: Orbit's purge path
rejects every package with `is_installed == False`. A removed package in dpkg's
config-files state therefore cannot be purged through this path. That is narrower
than users normally expect from purge, in addition to the missing autopurge UI.

Evidence: [Orbit CLI](../orbit_gtk/cli.py),
[transaction planning](../orbit_gtk/backend/transactions.py),
[helper configuration and repair](../orbit_gtk/backend/helper.py),
[local archives](../orbit_gtk/backend/local_deb.py),
[Nala install logic](../nala/legacy/python/nala/install.py),
[Nala command routing](../nala/legacy/python/nala/nala.py).

## History, cleanup and package information

| Capability | Nala reference | Orbit today | Priority/action |
| --- | --- | --- | --- |
| Transaction history | Own structured history, details, undo/redo, clear and explicitly-installed tracking | Reads APT/Nala logs; chips and grouped details; no replay or Orbit-owned durable operation journal | P1: journal operation lifecycle first; reviewed replay later |
| History depth | Dedicated history store | Loads at most 200 combined entries and only current plus first rotated APT log variants | P2: expose limits and paginate older available logs |
| Unused-package cleanup | Autoremove, autopurge, residual-config cleanup | Reviewed autoremove keeps configuration; no autopurge | P1: distinguish remove, purge and residual configs |
| Cache cleanup | Archives and binary caches; optional lists and fetched sources | Archive/list cleanup and scoped mirror clearing; no binary-cache cleanup target | P3: clarify scope; avoid unnecessary cache deletion |
| Dependency details | `show` includes dependencies, recommendations, conflicts, provides and other fields | Version, description, sizes, source, section, reason, artwork and homepage | P1: dependency/provider/conflict sections and navigable related packages |
| Search/list | Regex/glob modes; names-only, installed/upgradable, versions, architecture and virtual filters | Ranked substring search capped at 200; separate installed/updates views | P2: filters, explicit pagination and optional pattern mode |
| Versions/policy | Legacy show all versions; Rust checkout additionally implements `policy` | Installed and candidate version only; no pin-priority/origin table | P2: source/version/policy details before version selection |
| Localization | Python gettext catalogues and Rust locale resources | English UI strings without a translation pipeline | P2: translation infrastructure and localized UI tests |

History replay is not filesystem rollback: old packages can disappear, dependency
relationships change, and removed configuration may not be recoverable. A GUI
must describe those limits before calling an action “Undo”. Do not delete shared
APT or Nala logs just to implement a cosmetic history-clear button.

Evidence: [Orbit history reader](../orbit_gtk/backend/history.py),
[package details](../orbit_gtk/ui/package_dialog.py),
[search](../orbit_gtk/backend/apt_cache.py),
[cleanup targets](../orbit_gtk/backend/apt_manager.py),
[Nala history](../nala/legacy/python/nala/history.py),
[Nala show](../nala/legacy/python/nala/show.py),
[Rust policy](../nala/src/cmd/policy.rs).

## Mirrors

Both support Debian, Ubuntu and Devuan catalogue families. Orbit additionally
supports Mint and Kali and keeps archive/suite selections separate. The Ubuntu
catalogue implementation is not identical: legacy Nala uses Launchpad RSS, the
Rust reference uses its API, and Orbit currently parses its HTML listing. A
structured provider feed would reduce Orbit's sensitivity to page-layout changes.

| Missing/partial Orbit control | Nala equivalent | Suggested GUI |
| --- | --- | --- |
| Country filtering | Repeatable `--country` | Country multiselect; provider metadata must retain country information |
| Exclusive HTTPS | `--https-only` | HTTPS-only checkbox, with clear empty-result feedback |
| Arbitrary mirror subset | Interactive lists/ranges | Checkboxes and one Apply selection action; repeated Use currently replaces the set |
| Arbitrary result/selection count | `--fetches` | Configurable count; Orbit currently offers 3/5/8/16 |
| Explicit provider/release override | `--debian`, `--ubuntu`, `--devuan` | Advanced selection with compatibility checks, never a silent release migration |
| Source-package entries | `--sources` | Optional deb-src entries with source-index validation |
| Component changes | `--non-free` | Separate repository component editor; preserve existing values by default |
| Unattended selection | `--auto` | Optional GUI preselection; privileged changes should still be reviewed |

Orbit validates the expected Origin, suite, components, architectures and expiry,
and rechecks selected mirrors before saving. These are compatibility checks, not
signature verification; APT retains responsibility for authentication. Nala's
legacy benchmark mostly checks successful downloads; the Rust branch validates
basic Release structure. Those differences support specific claims about checking,
not a blanket claim that one application is more secure.

Evidence: [Orbit catalogues](../orbit_gtk/backend/mirror_catalogues.py),
[mirror UI](../orbit_gtk/ui/pages/mirrors.py),
[Nala fetch](../nala/legacy/python/nala/fetch.py),
[Rust mirror providers](../nala/src/cmd/fetch/mirrors.rs),
[Rust Release checks](../nala/src/cmd/fetch/score.rs).

## Download behavior, recovery and automation

Nala's Python reference has an asynchronous downloader, per-domain concurrency
control, alternative URLs, hash checks and fallback to APT for eligible failures.
Orbit delegates package transfer and verification to APT via `cache.commit`.
APT itself can perform concurrent work; Orbit must not be described as necessarily
serial. Nor does Nala's custom downloader establish that it is faster on every
connection. Compare identical package sets, cold/warm caches, sources, bandwidth,
latency, loss and proxy settings before changing download architecture.

Orbit exposes cancellation before applying a plan and for mirror benchmarking,
but no user-facing package-download cancellation after approving a transaction.
Nala's downloader handles interrupts. Add phase-aware cancellation before dpkg;
do not blindly terminate an installation to emulate a terminal interrupt.

Orbit deliberately survives a closed GUI pipe without aborting dpkg, but lacks a
durable operation journal, reconnectable service and completion recovery UI.
This is an Orbit desktop acceptance gap, not a claim that Nala offers a comparable
GUI reconnection service. Also, Orbit holds the APT system lock while the review
is displayed, with a five-minute approval timeout: explain that state and lock
contention in the GUI rather than presenting an unexplained failure.

Nala supports CLI-only/headless workflows, shell completion, debug/verbose output,
assume-yes/no and arbitrary APT/dpkg option passthrough. Orbit's CLI routes into
GUI workflows and implements only a subset of arguments. Headless parity is an
optional product decision. Raw terminal formatting, animation controls and
arbitrary trust-bypass options should not become mandatory GUI features.

Evidence: [Nala downloader](../nala/legacy/python/nala/downloader.py),
[Orbit transaction commit](../orbit_gtk/backend/transactions.py),
[operation view](../orbit_gtk/ui/operation_view.py),
[helper lifecycle](../orbit_gtk/backend/helper.py).

## What Orbit already contributes

- Native GUI browsing, app icons/screenshots and installed-package lazy rows.
- Inline per-package download/unpacking/configuration state and graphical plans.
- History action chips, space-reclaim estimates and visible cached/offline states.
- Per-archive mirror discovery on derivatives, Mint/Kali catalogues, and scoped
  source-file updates preserving configured components and signing keys.
- Conservative transaction boundaries and explicit reviews.

These are implemented capabilities, not proof that every desktop, screen reader,
maintainer script or weak-network scenario has passed acceptance.

## Verification and remaining evidence

This audit reproduced CLI rejection for `autopurge`, `install --download-only`,
`upgrade --exclude`, `history undo last`, and `fetch --country`. A small isolated
package fixture reproduced the residual-config purge rejection. No privileged
transactions were run for this comparison; previous regression results are not
presented as new tests or comparative Nala tests.

The suite now contains 44 opt-in GUI test methods and one opt-in loopback HTTP
fault test. A default run therefore skips 45 unless enabled (the preceding run
reported 44 before the newest GUI method was added). The latest focused mirror
run executed four GUI tests; that is not the full GUI suite. The artwork test may
also skip within an enabled GUI run if its required cached metadata is absent.

Full Devuan distro acceptance, ARM/ports, real desktop Polkit/Wayland, broader
maintainer-script/debconf cases, abrupt termination/recovery, accessibility, and
controlled Nala/Orbit performance comparisons remain open.

Holds editing, changelog browsing, Flatpak/Snap, offline system upgrades and a
release-migration wizard are useful product ideas, but are not established Nala
0.16.0 command advantages in this audit. Do not count them as proven Nala parity
deficits merely because Orbit lacks them.

## Recommended order

1. Correct residual-config purge; add reviewed dependency repair and clarify
   noninteractive configuration/restart behavior.
2. Add dependencies/versions/source details, then reviewed version selection,
   upgrade exclusions and a GUI transaction basket.
3. Add autopurge, download-only, and multi-local-archive resolution.
4. Complete mirror controls, starting with HTTPS-only, country and multiselect.
5. Add a durable operation journal before implementing history replay or recovery.
6. Add localization and expanded discovery; measure download performance before
   deciding whether a separate downloader is justified.

No numerical overall score is assigned: there is no agreed weighting or completed
comparative acceptance/performance suite from which to derive one honestly.

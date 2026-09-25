# Validation and engineering assessment

Assessment date: 2026-09-23. Environment: Debian forky/sid, system Python 3.14.7,
python-apt, GTK4 and libadwaita 1.9. GUI checks use Xvfb with software rendering.

**Current adversarial release-readiness assessment: 9.0/10.**
See [the 24 September audit](AUDIT-2026-09-24.md) for fixed issues, test evidence,
missing features and the weighted rubric. This supersedes the earlier 9.2/10
rating, which covered a narrower development scope and was too optimistic as a
release assessment. The historical validation notes below remain useful context.

## Earlier validation (23 September)

Initial checks: **27 backend tests and 8 GTK integration tests passed**, plus the
real isolated transaction/helper lifecycle. Ruff lint/format checks, shell syntax,
`git diff --check`, and uv source/wheel builds passed. Screenshots of the actual
Updates page and the isolated inline-progress test were visually inspected.

- Fixed the `orb_gtk` directory versus `orbit_gtk` imports/entry-point mismatch.
- uv editable installation, wheel and source-distribution builds.
- Backend regression suite: package validation, dependency guards, transaction
  approval/cancellation/failure, progress, mirror input validation and duplicate-source filtering,
  historical formats, thread-local errors, and read-only live APT queries.
- GTK regression suite: real data pages and search; package details and narrow
  navigation; inline progress with independent architecture-qualified package
  identities; real subprocess pipes carrying fragmented UTF-8 events; cancellation,
  failure states, and the close guard.
- Real isolated lifecycle: install fixture 1.0, update to 2.0, then remove it.
  Verified file contents and plans, with actual Unpacking/Configuring/Removing
  callbacks. Also verified the real privileged helper's cancel/apply handshake,
  JSON framing, captured native logs, and removal.
- The application itself uses no fabricated package, history-duration, bandwidth,
  or mirror-reliability values. Synthetic inputs exist only in regression tests.

## Measured performance

On this machine, with 3,699 installed packages and 138 upgrade candidates:

| Read operation | Before origin-label fix | After origin-label fix |
| --- | ---: | ---: |
| First installed snapshot | 82.48 s | 0.415 s |
| Upgrade snapshot | 2.361 s | 0.165 s |
| Search for `bash` | 5.58 s | 1.232 s |

These are historical measurements before the added upgrade-policy simulation and
include normal machine/cache variation.
The large installed-list difference came from repeated repository trust checks
inside python-apt's `Origin` construction. Display labels now read cached source
metadata; authenticated installation is still enforced by APT at commit time.

## Remaining release limits

- The development checkout launches its Python helper through Polkit's generic
  administrator authorization. A distributable system package should install a
  root-owned helper and a narrowly scoped Polkit action. Do not install an
  authorization rule that bypasses authentication for this development launcher.
- A real desktop's authentication-agent interaction has not been automated.
  The helper and GUI transport were tested separately and together with isolated
  package operations, but no host upgrade was performed.
- Live internet mirror reachability, hardware acceleration and other distro/GTK
  combinations need release testing. Mirror parsing, save-without-refresh, and duplicate filtering
  have regression coverage; mirror selection deliberately supports Debian only.
- The local integration repository is explicitly trusted test data and has no
  package scripts. It proves the APT/dpkg lifecycle, not repository-signature
  infrastructure, service restarts, or arbitrary debconf interactions.
- Configuration files are kept noninteractively and NEEDRESTART uses automatic
  mode. A future GUI configuration/debconf editor is separate work.
- APT installation percentages are overall values. Per-package bars show
  measured download fractions and indeterminate installation activity; they do
  not invent a percentage for unpacking/configuration.

No installed host packages or host APT source files were changed during this work.

## Follow-up polish

- Mirror saving now follows Nala fetch's save-only behavior. It filters exact
  enabled URI/suite duplicates in both `.list` and `.sources` files, excludes
  Orbit's own file when comparing, and does not automatically refresh APT.
- Mirror save results are inline rather than a progress dialog.
- Installed packages append automatically near the scroll boundary. Stale
  scheduled appends are discarded after filtering.
- Search install/installed controls use matching widths and centered alignment.
- History uses semantic colors plus icons/text, so meaning does not rely on color alone.
- Per-package upgrade bars remain hidden before actual work begins.

A score of 10 is not justified by these checks: production Polkit packaging,
real desktop authentication, arbitrary package scripts, and cross-distro/user
acceptance testing remain outside the verified scope.

## Mirror and kept-back status corrections

- Configured mirrors include enabled system/Nala sources for the active suite;
  green text badges identify ownership. Only Orbit-owned entries are removable.
- Benchmark metadata is attached before sorted insertion, so selecting the best
  mirrors uses the actual latency order. Badges refresh after saving or clearing.
- Upgrade snapshots simulate the same normal APT upgrade policy in memory and
  clear all marks afterward. Excluded candidates retain amber kept-back badges
  after reload, including transactions with no actionable upgrades.
- Structured kept-back events use architecture-qualified package identities.
  Completion, cancellation and failure labels use green, amber and red.

## Application artwork

- Browse, Installed, Updates and package details map binary package names to the
  local AppStream catalogue, including architecture-qualified package names.
- Repository cached artwork is preferred, followed by referenced installed theme
  icons and a generic package fallback. No remote image URLs are fetched.
- Catalogue indexing runs on data workers; artwork decodes on two workers and is
  cached with a 256-entry bound. GTK updates use weak widget references on its
  main context. Icon slots retain their dimensions while loading.
- Validated 30 backend, 4 icon-mapping and 12 GTK tests (46 passing), including
  real Debian GIMP artwork, corrupt images, unavailable metadata and cache reuse.
  Verified the Browse screenshot, Ruff checks and wheel/source builds.

## Package description screenshots

- Shared AppStream metadata now supplies icons and screenshot URLs/captions.
  Screenshot galleries are omitted when metadata is absent.
- One screenshot loads at a time when details open, with asynchronous decoding,
  bounded caching, navigation, captions, loading state and retry on failure.
  Downloads accept HTTPS only, validate redirects, and enforce size/time limits.
- 39 backend/metadata/transfer tests and 13 GTK tests passed (52 total). A real
  Debian GIMP screenshot was fetched and visually verified in PackageDialog.
  Added coverage for stale results, unavailable images and retry/navigation.

## Structured transaction history

- Replaced expanded text dumps with semantic count chips, action groups and
  virtualized package lists showing names and versions. Technical transaction
  details are collapsed separately. Groups are populated on first expansion.
- Preserved purge, reinstall and downgrade records independently in APT/Nala
  parsing, including mixed transactions and Nala's purge flag.
- 41 backend/metadata tests and 14 GUI tests passed (55 total), including a
  500-package history group and repeated expansion. Real local history was
  visually checked at 820px and 420px widths.

# Distribution-specific mirrors

Updated 7 October 2026.

Orbit supports mirror discovery and selection for **Debian, Ubuntu, Devuan, Linux Mint
and Kali archives**, including those configured on other derivatives. Open **Mirrors**, select an **Archive and suite**, then choose
**Benchmark mirrors**. Results show compatible reachable archives ordered by
Release-file transfer time. Choose **Use** or **Use best 3** (the menu also offers
5, 8 and 16).

## Selecting and changing mirrors

1. Open **Mirrors** and choose **Archive and suite**.
2. Select **Benchmark mirrors** and wait for compatible results, or use **Stop**
   to end the benchmark.
3. Choose **Use** for one mirror or **Use best 3** for a selection. Administrator
   authentication is required to save source changes.
4. Repeat for another archive/suite if needed. Saving Mint's archive does not
   overwrite its Ubuntu or Debian base selection.
5. Refresh package lists separately when ready to use the added sources.

**Use** and **Use best** replace the selected archive/suite's Orbit mirror set;
they do not append indefinitely. **Remove** deletes one Orbit-owned entry, while
**Clear selection** removes all Orbit entries for the currently selected
archive/suite. Neither control removes system-owned entries.

## Separate repositories

The selector comes from enabled binary APT sources, not a guessed release name.
Both traditional `.list` files and Deb822 `.sources` files are supported.
Cached Release/InRelease Origin metadata identifies each archive independently.
The OS name only influences display order; `ID_LIKE` never determines a suite.
When cached metadata is absent, recognized official hosts or distro signing-key
filenames can identify an archive. Existing metadata with an unknown, missing or
duplicate Origin, or unreadable metadata, is skipped rather than overridden by
that fallback. Explicit vendor keys remain excluded even with a recognized Origin.
Discovery works offline; missing custom-mirror metadata may require a refresh.
This broadens archive discovery, not the list of fully tested distributions.

- Devuan uses its official catalogue and preserves the configured `merged` or
  Devuan-only (`devuan`) archive layout. These are separate selections even when
  they use the same suite. Inactive and non-HTTP(S) catalogue entries are skipped.
- Debian exposes its configured suites, including enabled updates/backports.
- Ubuntu exposes its configured base and update/backport pockets independently.
- Linux Mint exposes its own archive separately from its configured Ubuntu base.
  LMDE's Debian base is also recognized; that path has fixture coverage, not a
  separate LMDE desktop acceptance run.
- Kali archives use Kali mirrors; separately configured Debian archives use Debian mirrors.

Security suites ending in `-security` and legacy `/updates` layouts are excluded
from selection; existing security sources remain unchanged. Third-party feeds
and PPAs are not mirror candidates. Unrecognized archives, ambiguous conflicting
source settings, inline signing keys and unsupported architecture modifiers are
not guessed or silently rewritten. If a custom mirror cannot be recognized from
its configured distro key or cached Origin metadata, refresh its existing package
lists first or use the distribution's source settings tool.

## Official catalogues

| Archive | Catalogue used |
| --- | --- |
| Debian | [Debian mirror master list](https://mirror-master.debian.org/status/Mirrors.masterlist) |
| Devuan | [Devuan mirror catalogue](https://pkgmaster.devuan.org/mirror_list.txt) |
| Ubuntu | [Launchpad Ubuntu archive mirrors](https://launchpad.net/ubuntu/+archivemirrors) |
| Linux Mint | The **Repository mirrors** section of [Mint's mirror list](https://www.linuxmint.com/mirrors.php), excluding ISO downloads |
| Kali | The package archive list linked by [Kali's official mirror documentation](https://www.kali.org/docs/community/kali-linux-mirrors/), at [README?mirrorlist](https://http.kali.org/README?mirrorlist) |

A catalogue entry is not enough to become selectable. Orbit fetches the selected
suite's Release metadata and checks Origin, suite/codename, SHA256 index entries,
configured components, architecture coverage and metadata expiry when provided.
Debian's master-list architecture filter is also retained. Mirrors missing any
required architecture or component are excluded, so there may be no compatible
result for a particular configuration. Architecture-specific/ports coverage
depends on the provider catalogue; ARM desktop acceptance is not yet established.

Checks prefer HTTPS where available. Requests have bounded body sizes and socket
timeouts, and the benchmark can be stopped. Catalogue/probe failures are shown
in the GUI without blocking package browsing. Stop discards pending results;
in-flight requests finish at their timeout. OS DNS resolution is not guaranteed
to obey an absolute wall-clock deadline.

## What saving changes

Saving rechecks every selected mirror in the privileged helper, then updates only
the selected archive/suite stanza in
`/etc/apt/sources.list.d/orbit-mirrors.sources`. The configured components,
architecture restrictions and explicit signing key are preserved. Other Orbit
selections and all system/Nala source files remain unchanged. **Clear selection**
removes only that archive/suite's Orbit stanza.

The save operation briefly shows **Checking mirrors** while it re-fetches Release
metadata. It does not download full package indexes or install packages. If any
selected mirror fails validation, the source file is left unchanged; benchmark
again or retry after connectivity returns.

Saving does not run a package-list refresh. Refresh separately when ready.
Existing system mirrors remain enabled, so adding faster mirrors does not
promise that APT always downloads packages from them. In-use badges distinguish
Orbit-owned entries from existing system entries.

Release compatibility checks are not cryptographic authentication. APT still
verifies repository signatures during refresh/download using the configured
trust settings; Orbit never adds `Trusted: yes` or disables signature checks.
The benchmark measures Release-file latency/transfer time, not sustained package
bandwidth or an assurance that every package is present.

## Validation

Regression tests cover provider separation, Mint versus Ubuntu/LMDE base sources,
source options, vendor exclusion, incompatible/expired Release metadata,
scoped saves/clears, preservation after failed validation, catalogue parsing and
stale GTK controls. All five distro targets passed the eight-stage matrix. Final
offline checks on rebuilt images also verified real source discovery, APT parsing
of rendered source entries, 12 provider regressions and 3 GTK mirror tests.
See [the distro report](DISTRO-TESTING.md) for runtime versions and limitations.
Live catalogue and sampled Release checks also succeeded for all four providers
on 7 October 2026; reachability and catalogue counts change over time.

Devuan has parser, discovery, archive-isolation and Release-validation regression
coverage plus a live catalogue/Release smoke check. It has not yet undergone the
full distro container matrix or desktop acceptance tests. All three catalogue
families in the referenced Nala fetch implementation are now supported: Debian,
Ubuntu and Devuan; Mint and Kali remain available.

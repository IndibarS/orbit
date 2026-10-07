"""Resolve several local archives together using an ephemeral, explicit local APT source."""

import hashlib
import shutil
import tempfile
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import apt
import apt_inst
import apt_pkg

from orbit_gtk.backend.local_deb import staged_archive
from orbit_gtk.backend.progress import CacheProgress, DownloadProgress
from orbit_gtk.backend.transactions import PACKAGE_NAME, TransactionRejected, execute


def install_batch(paths, names, emit, approve, cancelled=None):
    if not paths:
        raise TransactionRejected("Choose at least one local archive.")
    with ExitStack() as stack:
        temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="orbit-archives-")))
        repo = temporary / "repo"
        repo.mkdir()
        records, versions = [], {}
        for index, path in enumerate(paths):
            staged = stack.enter_context(staged_archive(path))
            target = repo / f"package-{index}.deb"
            shutil.copyfile(staged, target)
            control = apt_inst.DebFile(str(target)).control.extractdata("control").decode()
            fields = apt_pkg.TagSection(control)
            name, arch, version = fields["Package"], fields["Architecture"], fields["Version"]
            key = name if arch == "all" else f"{name}:{arch}"
            if not PACKAGE_NAME.fullmatch(key) or key in versions:
                raise TransactionRejected("Local archives contain invalid or duplicate packages.")
            versions[key] = version
            hashes = {algorithm: hashlib.new(algorithm) for algorithm in ("sha256", "sha512")}
            with target.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    for digest in hashes.values():
                        digest.update(chunk)
            records.append(
                control.strip()
                + f"\nFilename: {target.name}\nSize: {target.stat().st_size}\nSHA256: {hashes['sha256'].hexdigest()}\nSHA512: {hashes['sha512'].hexdigest()}\n"
            )
        packages = "\n".join(records).encode()
        (repo / "Packages").write_bytes(packages)
        (repo / "Release").write_text(
            "Origin: OrbitLocal\nLabel: OrbitLocal\nSuite: local\nCodename: local\nDate: "
            + datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
            + f"\nSHA256:\n {hashlib.sha256(packages).hexdigest()} {len(packages)} Packages\n"
        )
        source = temporary / "local.list"
        source.write_text(f"deb [trusted=yes] file:{repo} ./\n")
        sources = temporary / "sources.list.d"
        sources.mkdir()
        original_parts = Path(apt_pkg.config.find_dir("Dir::Etc::sourceparts"))
        if original_parts.is_dir():
            for index, original in enumerate(original_parts.iterdir()):
                if original.is_file() and original.suffix in {".list", ".sources"}:
                    shutil.copyfile(original, sources / f"source-{index}{original.suffix}")
        shutil.copyfile(source, sources / "orbit-local.list")
        lists = temporary / "lists"
        lists.mkdir()
        (lists / "partial").mkdir()
        original_lists = Path(apt_pkg.config.find_dir("Dir::State::Lists"))
        for original in original_lists.iterdir():
            if original.is_file() and original.name != "lock":
                (lists / original.name).symlink_to(original)
        overrides = {
            "Dir::Etc::sourceparts": str(sources),
            "Dir::State::Lists": str(lists),
            "Dir::Cache::pkgcache": "",
            "Dir::Cache::srcpkgcache": "",
        }
        previous = {key: apt_pkg.config.find(key) for key in overrides}
        try:
            for key, value in overrides.items():
                apt_pkg.config.set(key, value)
            with apt.Cache(progress=CacheProgress(emit)) as cache:
                cache.update(
                    fetch_progress=DownloadProgress(emit, cancelled=cancelled),
                    sources_list=str(source),
                )
            emit(
                "warning",
                message="Local archives are not authenticated by repository signatures. Only install files you trust. APT will review the combined dependency changes.",
            )
            return execute(
                "install",
                list(dict.fromkeys([*versions, *names])),
                emit,
                approve,
                {"versions": versions},
                cancelled=cancelled,
            )
        finally:
            for key, value in previous.items():
                apt_pkg.config.set(key, value)

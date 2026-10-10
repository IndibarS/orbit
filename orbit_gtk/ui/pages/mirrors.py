"""Manage distro-specific mirrors in Orbit's optional source file."""

from __future__ import annotations

import threading
from dataclasses import replace
from urllib.parse import urlparse

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.mirror_benchmark import MirrorBenchmarkWorker, get_flag_for_mirror
from orbit_gtk.backend.mirrors import (
    UnsupportedMirrorDistribution,
    flag,
    source_profiles,
    source_settings,
)
from orbit_gtk.backend.models import MirrorInfo
from orbit_gtk.i18n import tr
from orbit_gtk.ui.operation_view import OperationView
from orbit_gtk.ui.widgets import action_row


class MirrorsPage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, window: object) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = window
        self._operation = None
        self._loaded = False
        self._profiles = []
        self._settings = None
        self._updating_selector = False
        self._active_urls: list[str] = []
        self._configured_urls: set[str] = set()
        self._load_generation = 0
        self._active_rows: list[Gtk.Widget] = []
        self._worker: MirrorBenchmarkWorker | None = None
        self._run_id = 0
        self._total = 0

        self._banner = Adw.Banner(revealed=False)
        self._banner.connect("button-clicked", self._on_banner_clicked)
        self.append(self._banner)
        self._operation_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        operation_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=360,
        )
        operation_scroll.set_child(self._operation_slot)
        self.append(operation_scroll)
        self._progress = Gtk.ProgressBar(visible=False)
        self._progress.set_margin_start(12)
        self._progress.set_margin_end(12)
        self._progress.set_margin_bottom(6)
        self.append(self._progress)

        self._unsupported = Adw.StatusPage(
            title=tr("Mirror selection unavailable"),
            icon_name="network-server-symbolic",
            description=tr(
                "No recognized Debian, Ubuntu, Devuan, Linux Mint or Kali archives were found in your enabled APT sources. Refresh package lists if metadata is missing, then try again. Unrecognized repositories remain unchanged and can still serve package operations."
            ),
            visible=False,
            vexpand=True,
        )
        choose = Gtk.Button(label=tr("Choose a catalogue manually"), halign=Gtk.Align.CENTER)

        def manual(_):
            self._unsupported.set_visible(False)
            self._content_scroll.set_visible(True)
            self._manual_provider.set_selected(1)

        choose.connect("clicked", manual)
        self._unsupported.set_child(choose)
        self.append(self._unsupported)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self._content_scroll = scroll
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scroll)
        page = Adw.PreferencesPage()
        scroll.set_child(page)
        self._active_group = Adw.PreferencesGroup(
            title=tr("Configured mirrors"),
            description=tr(
                "Save selected mirrors to Orbit’s source file. Existing sources are preserved; refresh package lists separately."
            ),
        )
        repository_group = Adw.PreferencesGroup(
            title=tr("Repository"),
            description=tr(
                "Benchmark and select mirrors for one archive and suite at a time. Security sources remain unchanged."
            ),
        )
        self._repository = Adw.ComboRow(title=tr("Archive and suite"))
        self._repository.connect("notify::selected", self._select_repository)
        repository_group.add(self._repository)
        self._https_only = Adw.SwitchRow(
            title=tr("HTTPS only"), subtitle=tr("Do not fall back to unencrypted HTTP")
        )
        self._countries = Adw.EntryRow(title=tr("Country codes · optional, e.g. DE FR"))
        repository_group.add(self._https_only)
        repository_group.add(self._countries)
        advanced = Adw.ExpanderRow(
            title=tr("Advanced repository settings"),
            subtitle=tr(
                "Optional overrides affect Orbit sources only. A different suite changes available package versions."
            ),
        )
        self._manual_provider = Adw.ComboRow(
            title=tr("Catalogue override"),
            model=Gtk.StringList.new(
                ["Use selected archive", "Debian", "Ubuntu", "Devuan", "Linux Mint", "Kali"]
            ),
        )
        advanced.add_row(self._manual_provider)
        self._suite_override = Adw.EntryRow(title=tr("Suite override · leave empty to preserve"))
        self._components_override = Adw.EntryRow(
            title=tr("Components · space-separated; empty preserves")
        )
        self._sources = Adw.SwitchRow(title=tr("Include source-package repositories"))
        advanced.add_row(self._suite_override)
        advanced.add_row(self._components_override)
        advanced.add_row(self._sources)
        repository_group.add(advanced)
        page.add(repository_group)
        page.add(self._active_group)
        actions = Gtk.Box(spacing=8)
        self._benchmark = Gtk.Button(label=tr("Benchmark mirrors"), valign=Gtk.Align.CENTER)
        self._benchmark.add_css_class("suggested-action")
        self._benchmark.connect("clicked", self._on_benchmark)
        actions.append(self._benchmark)
        self._clear = Gtk.Button(label=tr("Clear selection"), valign=Gtk.Align.CENTER)
        self._clear.add_css_class("flat")
        self._clear.connect("clicked", self._on_clear)
        actions.append(self._clear)
        self._active_group.set_header_suffix(actions)

        self._results_group = Adw.PreferencesGroup(
            title=tr("Benchmark results"),
            description=tr(
                "Results are sorted by the measured time to fetch the active suite's Release file."
            ),
        )
        self._results_group.set_visible(False)
        page.add(self._results_group)
        self._results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._results.add_css_class("boxed-list")
        self._results.set_sort_func(self._sort_rows)
        results_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._result_actions = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self._count = Gtk.SpinButton.new_with_range(1, 16, 1)
        self._count.set_value(3)
        self._count.set_valign(Gtk.Align.CENTER)
        self._count.set_tooltip_text(tr("Number of fastest mirrors"))
        self._best = Gtk.Button(label=tr("Use fastest"), valign=Gtk.Align.CENTER)
        self._best.add_css_class("suggested-action")
        self._best.set_sensitive(False)
        self._best.connect("clicked", lambda _: self._use_best(self._count.get_value_as_int()))
        fastest = Gtk.Box(valign=Gtk.Align.CENTER)
        fastest.add_css_class("linked")
        fastest.append(self._count)
        fastest.append(self._best)
        self._result_actions.append(fastest)
        self._result_actions.append(Gtk.Box(hexpand=True))
        selected = Gtk.Button(label=tr("Apply selection"), valign=Gtk.Align.CENTER)
        selected.connect("clicked", self._apply_selection)
        self._result_actions.append(selected)
        results_content.append(self._result_actions)
        results_content.append(self._results)
        self._results_group.add(results_content)

    def load_data(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load_generation += 1
        threading.Thread(
            target=self._load_active,
            args=(self._load_generation,),
            name="orbit-read-mirrors",
            daemon=True,
        ).start()

    def invalidate(self) -> bool:
        self._loaded = False
        self.load_data()
        return False

    def _load_active(self, generation: int) -> None:
        try:
            profiles = source_profiles()
            selected = next(
                (
                    i
                    for i, p in enumerate(profiles)
                    if self._settings and p.repository == self._settings.repository
                ),
                0,
            )
            settings = profiles[selected]
            mirrors = self.apt_manager.get_orbit_mirrors(settings)
            existing = set(settings.uris)
        except UnsupportedMirrorDistribution:
            GLib.idle_add(self._show_unsupported)
            return
        except (OSError, ValueError) as error:
            GLib.idle_add(self._load_failed, generation, str(error))
            return
        GLib.idle_add(self._show_profiles, profiles, selected, mirrors, existing, generation)

    def _show_profiles(self, profiles, selected, mirrors, existing, generation):
        if generation != self._load_generation:
            return False
        self._profiles = profiles
        self._settings = profiles[selected]
        self._updating_selector = True
        self._repository.set_model(Gtk.StringList.new([p.label for p in profiles]))
        self._repository.set_selected(selected)
        self._updating_selector = False
        self._unsupported.set_visible(False)
        self._content_scroll.set_visible(True)
        self._active_group.set_title(f"Configured mirrors · {self._settings.label}")
        self._banner.set_revealed(False)
        self._repository.set_sensitive(True)
        self._benchmark.set_sensitive(self._worker is None and self._operation is None)
        self._show_active(mirrors, existing, generation)
        return False

    def _select_repository(self, row, _property):
        if self._updating_selector or row.get_selected() >= len(self._profiles):
            return
        self._settings = self._profiles[row.get_selected()]
        self._run_id += 1
        if self._worker:
            self._worker.stop()
            self._finish_benchmark("Benchmark stopped")
        while child := self._results.get_first_child():
            self._results.remove(child)
        self._results_group.set_visible(False)
        self._banner.set_revealed(False)
        self._clear.set_sensitive(False)
        self._benchmark.set_sensitive(False)
        self._best.set_sensitive(False)
        self._active_urls = []
        self._configured_urls = set()
        for active_row in self._active_rows:
            self._active_group.remove(active_row)
        self._active_rows.clear()
        self.invalidate()

    def _show_unsupported(self):
        self._unsupported.set_visible(True)
        self._content_scroll.set_visible(False)
        self._banner.set_revealed(False)
        self._progress.set_visible(False)
        self._loaded = True
        return False

    def _load_failed(self, generation: int, message: str) -> bool:
        if generation == self._load_generation:
            self._loaded = False
            self._settings = None
            self._benchmark.set_sensitive(False)
            self._clear.set_sensitive(False)
            self._best.set_sensitive(False)
            self._repository.set_sensitive(False)
            self._banner.set_title(f"Could not read configured mirrors: {message}")
            self._banner.set_button_label("Dismiss")
            self._banner.set_revealed(True)
        return False

    def _show_active(self, mirrors: list[str], existing=(), generation=None) -> bool:
        if generation is not None and generation != self._load_generation:
            return False
        self._active_urls = mirrors
        self._configured_urls = set(mirrors) | set(existing)
        for row in self._active_rows:
            self._active_group.remove(row)
        self._active_rows.clear()
        self._clear.set_sensitive(bool(mirrors))
        urls = list(dict.fromkeys([*mirrors, *sorted(existing)]))
        if not urls:
            row = action_row(title=tr("No mirrors configured for the active suite"))
            self._active_group.add(row)
            self._active_rows.append(row)
        for url in urls:
            domain = urlparse(url).netloc or url
            row = action_row(title=f"{get_flag_for_mirror(url)} {domain}", subtitle=url)
            icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            icon.add_css_class("success")
            row.add_prefix(icon)
            badge = Gtk.Label(label="In use · Orbit" if url in mirrors else "In use · System")
            badge.add_css_class("success")
            badge.set_tooltip_text(
                tr(
                    "Enabled in APT sources for the active suite. Package lists are refreshed separately."
                )
            )
            row.add_suffix(badge)
            if url in mirrors:
                remove = Gtk.Button(label=tr("Remove"), valign=Gtk.Align.CENTER)
                remove.add_css_class("destructive-action")
                remove.connect("clicked", self._on_remove_one, url)
                row.add_suffix(remove)
            self._active_group.add(row)
            self._active_rows.append(row)
        row = self._results.get_first_child()
        while row:
            self._mark_result(row)
            row = row.get_next_sibling()
        return False

    def _mark_result(self, row) -> None:
        active = row._url in self._configured_urls
        row._use.set_label("In use" if active else "Use")
        row._use.set_sensitive(not active)
        if active:
            row._use.add_css_class("success")
        else:
            row._use.remove_css_class("success")
        row._use.set_tooltip_text(
            "Configured in Orbit’s source file"
            if row._url in self._active_urls
            else "Configured in existing APT sources"
            if active
            else "Save this mirror"
        )

    def _sort_rows(
        self, first: Gtk.ListBoxRow, second: Gtk.ListBoxRow, _data: object = None
    ) -> int:
        return (
            getattr(first, "_latency", float("inf")) > getattr(second, "_latency", float("inf"))
        ) - (getattr(first, "_latency", float("inf")) < getattr(second, "_latency", float("inf")))

    def _selected_settings(self):
        if self._manual_provider.get_selected():
            provider = ("debian", "ubuntu", "devuan", "linuxmint", "kali")[
                self._manual_provider.get_selected() - 1
            ]
            suite = self._suite_override.get_text().strip()
            return source_settings(f"manual:{provider}:{suite}:merged:explicit")
        return self._settings or source_settings()

    def _on_benchmark(self, _button: Gtk.Button) -> None:
        if self._worker or self._operation:
            return
        while child := self._results.get_first_child():
            self._results.remove(child)
        self._run_id += 1
        run_id = self._run_id
        self._total = 0
        self._benchmark.set_sensitive(False)
        self._best.set_sensitive(False)
        self._results_group.set_visible(False)
        self._progress.set_fraction(0.0)
        self._progress.set_visible(True)
        self._banner.set_title(tr("Downloading mirror catalogue…"))
        self._banner.set_button_label("Stop")
        self._banner.set_revealed(True)
        try:
            settings = self._selected_settings()
            suite = self._suite_override.get_text().strip() or settings.suite
            from orbit_gtk.backend.mirrors import validate_suite

            suite = validate_suite(suite)
            components = tuple(self._components_override.get_text().split()) or settings.components
            settings = replace(
                settings,
                suite=suite,
                components=components,
                source_packages=self._sources.get_active(),
            )
        except UnsupportedMirrorDistribution:
            self._show_unsupported()
            return
        except ValueError as error:
            self._finish_benchmark(str(error))
            return

        def current(callback):
            def wrapper(*args):
                if run_id != self._run_id:
                    return False
                return callback(*args)

            return wrapper

        self._repository.set_sensitive(False)
        self._banner.set_title(f"Downloading {settings.label} mirror catalogue…")
        self._worker = MirrorBenchmarkWorker(
            suite,
            settings=settings,
            https_only=self._https_only.get_active(),
            countries=tuple(self._countries.get_text().upper().split()),
            on_masterlist=current(self._on_masterlist),
            on_progress=current(self._on_progress),
            on_result=current(self._on_result),
            on_done=current(self._on_done),
            dispatch=GLib.idle_add,
        )
        self._worker.start()

    def _on_banner_clicked(self, _banner: Adw.Banner) -> None:
        if self._worker:
            self._worker.stop()
            self._worker = None
            self._run_id += 1
            self._finish_benchmark("Benchmark stopped")
        else:
            self._banner.set_revealed(False)

    def _on_masterlist(self, total: int) -> bool:
        self._total = total
        self._results_group.set_visible(True)
        self._banner.set_title(f"Testing {total} mirrors…")
        return False

    def _on_progress(self, completed: int, total: int, url: str) -> bool:
        if total:
            self._progress.set_fraction(completed / total)
        domain = urlparse(url).netloc or url
        self._banner.set_title(f"Tested {completed}/{total}: {domain}")
        return False

    def _on_result(self, info: MirrorInfo) -> bool:
        row = action_row(title=f"{flag(info.country_code)} {info.domain}", subtitle=info.url)
        latency = Gtk.Label(label=f"{info.latency_ms:.0f} ms")
        latency.add_css_class("numeric")
        latency.add_css_class("caption")
        row.add_suffix(latency)
        use = Gtk.Button(label=tr("Use"), valign=Gtk.Align.CENTER)
        use.add_css_class("flat")
        use.connect("clicked", self._on_use_one, info.url)
        row.add_suffix(use)
        row.add_prefix(Gtk.Image.new_from_icon_name("network-server-symbolic"))
        # Assign metadata before insertion: the list is already sorted.
        row._selected = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        row._selected.set_tooltip_text(tr("Include this mirror in the selection"))
        row.add_prefix(row._selected)
        row._latency = info.latency_ms
        row._url = info.url
        row._use = use
        self._mark_result(row)
        self._results.append(row)
        self._best.set_sensitive(True)
        return False

    def _on_done(self, results: list[MirrorInfo], error: str | None) -> bool:
        if error:
            self._finish_benchmark(f"Benchmark failed: {error}")
        else:
            reachable = sum(result.reachable for result in results)
            self._finish_benchmark(
                f"Done — {reachable} of {self._total} mirrors reachable"
                if reachable
                else "No matching mirrors responded. Check your connection, country filters and HTTPS settings."
            )
        return False

    def _finish_benchmark(self, message: str) -> None:
        self._repository.set_sensitive(self._operation is None)
        self._worker = None
        self._benchmark.set_sensitive(True)
        self._progress.set_visible(False)
        self._banner.set_title(message)
        self._banner.set_button_label("Dismiss")

    def _apply_selection(self, _button):
        urls = []
        child = self._results.get_first_child()
        while child:
            if child._selected.get_active():
                urls.append(child._url)
            child = child.get_next_sibling()
        if not urls or len(urls) > 16:
            self.window.show_toast("Select between 1 and 16 mirrors")
            return
        self._apply_mirrors(urls)

    def _on_use_one(self, _button: Gtk.Button, url: str) -> None:
        self._apply_mirrors([url])

    def _on_remove_one(self, _button: Gtk.Button, url: str) -> None:
        self._apply_mirrors([active for active in self._active_urls if active != url])

    def _on_clear(self, _button: Gtk.Button) -> None:
        self._apply_mirrors([])

    def _use_best(self, count: int) -> None:
        urls: list[str] = []
        child = self._results.get_first_child()
        while child and len(urls) < count:
            url = getattr(child, "_url", None)
            if url:
                urls.append(url)
            child = child.get_next_sibling()
        if not urls:
            self.window.show_toast("No reachable mirrors are available")
            return
        self._apply_mirrors(urls)

    def _apply_mirrors(self, urls: list[str], confirmed=False) -> None:
        try:
            settings = self._selected_settings()
        except (OSError, ValueError) as error:
            self.window.show_toast(str(error))
            return
        override = self._suite_override.get_text().strip()
        components = self._components_override.get_text().split()
        sources = self._sources.get_active()
        if urls and (override or components or sources) and not confirmed:
            dialog = Adw.AlertDialog(
                heading=tr("Save custom repository settings?"),
                body=f"Suite: {override or settings.suite}\nComponents: {' '.join(components or settings.components)}\nSource packages: {'yes' if sources else 'no'}\nExisting system sources remain enabled. APT may choose packages from either suite after refresh.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("save", "Save sources")
            dialog.set_close_response("cancel")
            dialog.connect(
                "response",
                lambda _, response: self._apply_mirrors(urls, True) if response == "save" else None,
            )
            dialog.present(self.window)
            return
        if urls:
            command = self.apt_manager.helper_command(
                "set-mirrors",
                "--repository",
                (
                    f"manual:{settings.provider}:{settings.suite}:{settings.archive}:explicit"
                    if self._manual_provider.get_selected()
                    else settings.repository
                ),
                "--suite",
                settings.suite,
                "--urls",
                *urls,
            )
            if override:
                command += ["--override-suite", override]
            if components:
                command += ["--components", *components]
            if sources:
                command += ["--sources"]
            title = f"Save mirrors · {settings.label}"
        else:
            command = self.apt_manager.helper_command(
                "clear-mirrors", "--repository", settings.repository
            )
            title = f"Clear mirrors · {settings.label}"
        if not self.window.claim_operation():
            return

        if self._worker:
            self._worker.stop()
            self._run_id += 1
            self._finish_benchmark("Benchmark stopped")
        self._repository.set_sensitive(False)

        def completed(success):
            if success:
                self.invalidate()

        def dismissed():
            self._operation_slot.remove(self._operation)
            self._operation = None
            self._repository.set_sensitive(True)
            self.window.release_operation()

        self._operation = OperationView(
            title, command, compact=True, on_done=completed, on_close=dismissed
        )
        self._operation_slot.append(self._operation)

"""Manage Orbit's own optional Debian mirror source safely."""

from __future__ import annotations

import threading
from urllib.parse import urlparse

from gi.repository import Adw, Gio, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.mirror_benchmark import MirrorBenchmarkWorker, get_flag_for_mirror
from orbit_gtk.backend.mirrors import existing_mirror_urls, flag, source_settings
from orbit_gtk.backend.models import MirrorInfo
from orbit_gtk.ui.operation_view import OperationView
from orbit_gtk.ui.widgets import action_row


class MirrorsPage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, window: object) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = window
        self._operation = None
        self._loaded = False
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

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scroll)
        page = Adw.PreferencesPage()
        scroll.set_child(page)
        self._active_group = Adw.PreferencesGroup(
            title="Configured mirrors",
            description="Save selected mirrors to Orbit’s source file. Existing sources are preserved; refresh package lists separately.",
        )
        page.add(self._active_group)
        actions = Gtk.Box(spacing=8)
        self._benchmark = Gtk.Button(label="Benchmark mirrors", valign=Gtk.Align.CENTER)
        self._benchmark.add_css_class("suggested-action")
        self._benchmark.connect("clicked", self._on_benchmark)
        actions.append(self._benchmark)
        self._clear = Gtk.Button(label="Clear Orbit mirrors", valign=Gtk.Align.CENTER)
        self._clear.add_css_class("flat")
        self._clear.connect("clicked", self._on_clear)
        actions.append(self._clear)
        self._active_group.set_header_suffix(actions)

        self._results_group = Adw.PreferencesGroup(
            title="Benchmark results",
            description="Results are sorted by the measured time to fetch the active suite's Release file.",
        )
        self._results_group.set_visible(False)
        page.add(self._results_group)
        self._results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._results.add_css_class("boxed-list")
        self._results.set_sort_func(self._sort_rows)
        self._results_group.add(self._results)
        result_actions = Gtk.Box(spacing=6)
        self._best = Gtk.Button(label="Use best 3", valign=Gtk.Align.CENTER)
        self._best.add_css_class("suggested-action")
        self._best.set_sensitive(False)
        self._best.connect("clicked", lambda _button: self._use_best(3))
        result_actions.append(self._best)
        menu = Gio.Menu()
        for count in (3, 5, 8, 16):
            menu.append(f"Use best {count}", f"mirrors.best_{count}")
        chooser = Gtk.MenuButton(menu_model=menu, icon_name="pan-down-symbolic")
        chooser.add_css_class("flat")
        chooser.set_sensitive(False)
        result_actions.append(chooser)
        self._choose_menu = chooser
        action_group = Gio.SimpleActionGroup()
        for count in (3, 5, 8, 16):
            action = Gio.SimpleAction.new(f"best_{count}", None)
            action.connect(
                "activate", lambda _action, _parameter, selected=count: self._use_best(selected)
            )
            action_group.add_action(action)
        self.insert_action_group("mirrors", action_group)
        self._results_group.set_header_suffix(result_actions)

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
            mirrors = self.apt_manager.get_orbit_mirrors()
            existing = existing_mirror_urls(source_settings().suite)
        except (OSError, ValueError) as error:
            GLib.idle_add(self._load_failed, generation, str(error))
            return
        GLib.idle_add(self._show_active, mirrors, existing, generation)

    def _load_failed(self, generation: int, message: str) -> bool:
        if generation == self._load_generation:
            self._loaded = False
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
            row = action_row(title="No mirrors configured for the active suite")
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
                "Enabled in APT sources for the active suite. Package lists are refreshed separately."
            )
            row.add_suffix(badge)
            if url in mirrors:
                remove = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
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

    def _on_benchmark(self, _button: Gtk.Button) -> None:
        if self._worker:
            return
        while child := self._results.get_first_child():
            self._results.remove(child)
        self._run_id += 1
        run_id = self._run_id
        self._total = 0
        self._benchmark.set_sensitive(False)
        self._best.set_sensitive(False)
        self._choose_menu.set_sensitive(False)
        self._results_group.set_visible(False)
        self._progress.set_fraction(0.0)
        self._progress.set_visible(True)
        self._banner.set_title("Downloading Debian's mirror catalogue…")
        self._banner.set_button_label("Stop")
        self._banner.set_revealed(True)
        try:
            suite = source_settings().suite
        except ValueError as error:
            self._finish_benchmark(str(error))
            return

        def current(callback):
            def wrapper(*args):
                if run_id != self._run_id:
                    return False
                return callback(*args)

            return wrapper

        self._worker = MirrorBenchmarkWorker(
            suite,
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
        use = Gtk.Button(label="Use", valign=Gtk.Align.CENTER)
        use.add_css_class("flat")
        use.connect("clicked", self._on_use_one, info.url)
        row.add_suffix(use)
        row.add_prefix(Gtk.Image.new_from_icon_name("network-server-symbolic"))
        # Assign metadata before insertion: the list is already sorted.
        row._latency = info.latency_ms
        row._url = info.url
        row._use = use
        self._mark_result(row)
        self._results.append(row)
        self._best.set_sensitive(True)
        self._choose_menu.set_sensitive(True)
        return False

    def _on_done(self, results: list[MirrorInfo], error: str | None) -> bool:
        if error:
            self._finish_benchmark(f"Benchmark failed: {error}")
        else:
            reachable = sum(result.reachable for result in results)
            self._finish_benchmark(
                f"Done — {reachable} of {self._total} mirrors reachable"
                if reachable
                else "No mirrors responded. Check your connection and retry the benchmark."
            )
        return False

    def _finish_benchmark(self, message: str) -> None:
        self._worker = None
        self._benchmark.set_sensitive(True)
        self._progress.set_visible(False)
        self._banner.set_title(message)
        self._banner.set_button_label("Dismiss")

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

    def _apply_mirrors(self, urls: list[str]) -> None:
        if urls:
            try:
                source_settings()
            except ValueError as error:
                self.window.show_toast(str(error))
                return
            command = self.apt_manager.helper_command(
                "set-mirrors", "--suite", source_settings().suite, "--urls", *urls
            )
            title = "Save Orbit mirrors"
        else:
            command = self.apt_manager.helper_command("clear-mirrors")
            title = "Clear Orbit mirrors"
        if not self.window.claim_operation():
            return

        def completed(success):
            if success:
                self.invalidate()

        def dismissed():
            self._operation_slot.remove(self._operation)
            self._operation = None
            self.window.release_operation()

        self._operation = OperationView(
            title, command, compact=True, on_done=completed, on_close=dismissed
        )
        self._operation_slot.append(self._operation)

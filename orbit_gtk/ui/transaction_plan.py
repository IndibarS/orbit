"""Searchable, recycled rows for the complete authoritative APT review plan."""

import math
from collections import Counter

from gi.repository import Gtk, Pango

from orbit_gtk.ui.widgets import status_chip

ACTIONS = {
    "remove": ("Remove", "warning"),
    "purge": ("Purge", "error"),
    "install": ("Install", "success"),
    "upgrade": ("Upgrade", "accent"),
    "reinstall": ("Reinstall", "accent"),
}


class TransactionPlan(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._progress_state = {}
        self._bound = {}
        self._names = set()
        self._changes = []
        self._filtered = []
        self._actions = [None]
        self._updating = False
        self._counts = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=5,
            row_spacing=4,
            column_spacing=4,
        )
        self.append(self._counts)
        filters = Gtk.Box(spacing=8)
        self._search = Gtk.SearchEntry(
            placeholder_text="Find a package in this plan…", hexpand=True
        )
        self._search.connect("search-changed", lambda _: self._filter())
        filters.append(self._search)
        self._action_filter = Gtk.DropDown(model=Gtk.StringList.new(["All actions"]))
        self._action_filter.set_tooltip_text("Filter changes by action")
        self._action_filter.connect("notify::selected", lambda *_: self._filter())
        filters.append(self._action_filter)
        self.append(filters)
        self._result_count = Gtk.Label(xalign=0)
        self._result_count.add_css_class("caption")
        self.append(self._result_count)

        self._model = Gtk.StringList.new([])
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self._list = Gtk.ListView(model=Gtk.NoSelection(model=self._model), factory=factory)
        self._list.add_css_class("boxed-list")
        self._scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_height=180,
            max_content_height=260,
            propagate_natural_height=True,
        )
        self._scroll.set_child(self._list)
        self.append(self._scroll)

    def set_changes(self, changes):
        # Validate the entire plan before showing any approval controls.
        if not isinstance(changes, list) or not changes:
            raise ValueError("A package plan must contain changes")
        for change in changes:
            if not isinstance(change, dict) or change.get("action") not in ACTIONS:
                raise ValueError("Unknown package action")
            if not isinstance(change.get("name"), str) or not change["name"]:
                raise ValueError("Missing package name")
            for field in ("old_version", "new_version"):
                if field not in change or (
                    change[field] is not None and not isinstance(change[field], str)
                ):
                    raise ValueError("Invalid package version")
        self._updating = True
        self._progress_state.clear()
        self._changes = [dict(change) for change in changes]
        self._names = {change["name"] for change in changes}
        counts = Counter(change["action"] for change in changes)
        while child := self._counts.get_first_child():
            self._counts.remove(child)
        self._actions = [None, *(action for action in ACTIONS if counts[action])]
        for action in self._actions[1:]:
            label, color = ACTIONS[action]
            self._counts.insert(status_chip(f"{counts[action]} to {label.lower()}", color), -1)
        self._action_filter.set_model(
            Gtk.StringList.new(
                ["All actions", *(ACTIONS[action][0] for action in self._actions[1:])]
            )
        )
        self._action_filter.set_selected(0)
        self._search.set_text("")
        self._updating = False
        self._filter()

    def _filter(self):
        if self._updating:
            return
        query = self._search.get_text().strip().casefold()
        action = self._actions[self._action_filter.get_selected()]
        self._filtered = [
            change
            for change in self._changes
            if (not action or change["action"] == action) and query in change["name"].casefold()
        ]
        self._model.splice(0, self._model.get_n_items(), [item["name"] for item in self._filtered])
        self._result_count.set_label(
            f"Showing {len(self._filtered):,} of {len(self._changes):,} changes. Apply uses the entire plan."
            if self._filtered
            else "No matching changes. Apply still uses the entire plan."
        )
        self._scroll.get_vadjustment().set_value(0)

    @staticmethod
    def _setup_row(_factory, item):
        row = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        badge = status_chip("", "accent")
        badge.set_size_request(84, -1)
        row.append(badge)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        details.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
        version = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        version.add_css_class("caption")
        version.add_css_class("dim-label")
        details.append(version)
        details.append(Gtk.Label(xalign=0, wrap=True, visible=False))
        details.append(Gtk.ProgressBar(visible=False))
        row.append(details)
        item.set_child(row)

    def _bind_row(self, _factory, item):
        change = self._filtered[item.get_position()]
        row = item.get_child()
        badge = row.get_first_child()
        label, color = ACTIONS[change["action"]]
        badge.set_label(label)
        for style in ("success", "warning", "error", "accent"):
            badge.remove_css_class(style)
        badge.add_css_class(color)
        name = badge.get_next_sibling().get_first_child()
        name.set_label(change["name"])
        versions = (
            f"{change['old_version'] or 'Not installed'} → {change['new_version'] or 'Removed'}"
        )
        name.get_next_sibling().set_label(versions)
        row.set_tooltip_text(f"{label} {change['name']}\n{versions}")
        self._bound[change["name"]] = item
        self._paint_progress(change["name"], item)

    def _unbind_row(self, _factory, item):
        for name, bound in list(self._bound.items()):
            if bound is item:
                del self._bound[name]

    def update_progress(self, event):
        name = event.get("package")
        if name not in self._names:
            return
        percent = event.get("percent") if event["event"] == "package-progress" else None
        if percent is not None and (
            not isinstance(percent, (int, float)) or not math.isfinite(percent)
        ):
            raise ValueError("Invalid package progress")
        self._progress_state[name] = (str(event.get("message", "Working…")), percent)
        if name in self._bound:
            self._paint_progress(name, self._bound[name])

    def _paint_progress(self, name, item):
        details = item.get_child().get_last_child()
        bar = details.get_last_child()
        label = bar.get_prev_sibling()
        state = self._progress_state.get(name)
        label.set_visible(state is not None)
        bar.set_visible(state is not None)
        if state:
            message, percent = state
            label.set_label(message)
            for color in ("accent", "success", "error"):
                label.remove_css_class(color)
            label.add_css_class(
                "success"
                if message == "Completed"
                else "error"
                if message.startswith("Stopped")
                else "accent"
            )
            if percent is None:
                bar.pulse()
            else:
                bar.set_fraction(max(0, min(1, percent / 100)))

    def pulse(self):
        for name, item in self._bound.items():
            if self._progress_state.get(name, (None, 0))[1] is None:
                self._paint_progress(name, item)

    def finish(self, success):
        for change in self._changes:
            self._progress_state[change["name"]] = (
                "Completed" if success else "Stopped — check details",
                100 if success else 0,
            )
        for name, item in self._bound.items():
            self._paint_progress(name, item)

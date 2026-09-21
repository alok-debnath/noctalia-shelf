#!/usr/bin/env python3
"""Floating drop window for the Noctalia Shelf plugin.

Noctalia plugin panels cannot take part in Wayland drag and drop, so this small
GTK4 window does it for them: files dropped on it are reported to the plugin,
and files on the shelf can be dragged out of it into any application.

The window never writes the shelf file. It reads the store the plugin passes in
(and follows changes to it), and reports every user action as one JSON object
per line on stdout. The plugin service owns the store and applies them:

    {"op": "ready"}                        window is up
    {"op": "add", "paths": [...]}          files were dropped or pasted
    {"op": "remove", "paths": [...]}       files were removed from the shelf
    {"op": "clear"}                        the shelf was cleared
    {"op": "bye"}                          window closed

Only one window runs at a time. Starting a second copy with --toggle closes the
running one; without --toggle it raises it.
"""

import argparse
import ctypes.util
import json
import os
import re
import subprocess
import sys

APP_ID = "dev.noctalia.Shelf"
LAYER_LIB = "libgtk4-layer-shell.so.0"


def _find_layer_shell():
    for d in ("/usr/lib64", "/usr/lib", "/usr/local/lib64", "/usr/local/lib",
              "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu"):
        p = os.path.join(d, LAYER_LIB)
        if os.path.exists(p):
            return p
    name = ctypes.util.find_library("gtk4-layer-shell")
    return name


def _maybe_reexec_with_layer_shell():
    # gtk4-layer-shell must be loaded before libwayland-client, which means
    # LD_PRELOAD for a Python process. Re-exec once with it set.
    if os.environ.get("SHELF_NO_LAYER_SHELL") or not os.environ.get("WAYLAND_DISPLAY"):
        return
    if "gtk4-layer-shell" in os.environ.get("LD_PRELOAD", ""):
        return
    lib = _find_layer_shell()
    if not lib:
        return
    env = dict(os.environ)
    env["LD_PRELOAD"] = (lib + " " + env.get("LD_PRELOAD", "")).strip()
    env["SHELF_LAYER_SHELL_TRIED"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, env)


if __name__ == "__main__" and not os.environ.get("SHELF_LAYER_SHELL_TRIED"):
    _maybe_reexec_with_layer_shell()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

LayerShell = None
if os.environ.get("SHELF_LAYER_SHELL_TRIED"):
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
    except (ValueError, ImportError):
        LayerShell = None

HOME = os.path.expanduser("~")
THUMB = 40


def emit(op, **fields):
    fields["op"] = op
    try:
        sys.stdout.write(json.dumps(fields) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass


# --- theme ------------------------------------------------------------------

DEFAULT_COLORS = {
    "primary": "#c2c1ff",
    "on_primary": "#2a2a60",
    "surface": "#131317",
    "surface_variant": "#1f1f25",
    "on_surface": "#e5e1e7",
    "on_surface_variant": "#c7c5d0",
    "outline": "#47464f",
    "error": "#ffb4ab",
}

GTK_CSS_KEYS = {
    "accent_color": "primary",
    "accent_fg_color": "on_primary",
    "window_bg_color": "surface",
    "card_bg_color": "surface_variant",
    "window_fg_color": "on_surface",
    "destructive_bg_color": "error",
}


def load_colors(overrides):
    colors = dict(DEFAULT_COLORS)
    # Noctalia's GTK template, when enabled, already holds the live palette.
    # The first definition of a name wins, matching how the file is generated.
    css = os.path.join(HOME, ".config/gtk-4.0/noctalia.css")
    seen = set()
    try:
        with open(css, encoding="utf-8") as fh:
            for name, value in re.findall(r"@define-color\s+(\w+)\s+(#[0-9a-fA-F]{6})", fh.read()):
                if name in GTK_CSS_KEYS and name not in seen:
                    seen.add(name)
                    colors[GTK_CSS_KEYS[name]] = value
    except OSError:
        pass
    for key, value in (overrides or {}).items():
        if key in colors and isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            colors[key] = value
    return colors


def rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def build_css(c):
    return f"""
window.shelf-window {{ background: transparent; }}
/* The theme's square drop highlight; the card draws its own rounded one. */
window.shelf-window *:drop(active) {{ box-shadow: none; outline: none; border-color: transparent; }}
.shelf-card {{
  background: {c['surface']};
  color: {c['on_surface']};
  border: 1px solid {rgba(c['outline'], 0.8)};
  border-radius: 22px;
  padding: 14px;
}}
window.shelf-window .shelf-card.drop-hover {{ border-color: {c['primary']}; }}
.shelf-title {{ font-weight: 700; font-size: 1.1em; }}
.shelf-count {{
  background: {rgba(c['primary'], 0.18)};
  color: {c['primary']};
  border-radius: 999px;
  padding: 1px 9px;
  font-weight: 700;
  font-size: 0.85em;
}}
.shelf-sub {{ color: {c['on_surface_variant']}; font-size: 0.85em; }}
.shelf-hint {{ color: {rgba(c['on_surface_variant'], 0.8)}; font-size: 0.8em; }}
button.shelf-icon {{
  min-width: 30px; min-height: 30px; padding: 0;
  border-radius: 999px; background: transparent; box-shadow: none; border: none;
  color: {c['on_surface_variant']};
}}
button.shelf-icon:hover {{ background: {rgba(c['on_surface'], 0.08)}; color: {c['on_surface']}; }}
button.shelf-icon.danger:hover {{ color: {c['error']}; background: {rgba(c['error'], 0.12)}; }}
.shelf-drag-all {{
  background: {c['primary']};
  color: {c['on_primary']};
  border-radius: 999px;
  padding: 5px 12px 5px 10px;
  font-weight: 700;
  font-size: 0.9em;
}}
.shelf-drag-all:hover {{ background: {rgba(c['primary'], 0.88)}; }}
.shelf-dropzone {{
  border: 2px dashed {rgba(c['on_surface_variant'], 0.35)};
  border-radius: 18px;
  padding: 26px 16px;
  background: {rgba(c['surface_variant'], 0.5)};
}}
.drop-hover .shelf-dropzone {{
  border-color: {c['primary']};
  background: {rgba(c['primary'], 0.10)};
}}
.shelf-dropzone-icon {{ color: {c['primary']}; }}
.shelf-dropzone-title {{ font-weight: 700; }}
list.shelf-list {{ background: transparent; }}
list.shelf-list > row {{
  border-radius: 14px; padding: 6px 6px 6px 8px; margin: 1px 0;
  background: transparent;
}}
list.shelf-list > row:hover {{ background: {rgba(c['on_surface'], 0.06)}; }}
list.shelf-list > row:selected {{ background: {rgba(c['primary'], 0.18)}; color: {c['on_surface']}; }}
list.shelf-list > row .row-remove {{ opacity: 0; }}
list.shelf-list > row:hover .row-remove {{ opacity: 1; }}
.row-name {{ font-weight: 600; }}
.row-meta {{ color: {c['on_surface_variant']}; font-size: 0.82em; }}
.row-missing .row-name {{ color: {c['error']}; }}
.row-thumb {{ border-radius: 10px; background: {rgba(c['surface_variant'], 0.9)}; }}
.row-thumb-icon {{ color: {c['primary']}; }}
.drop-banner {{
  background: {c['primary']};
  color: {c['on_primary']};
  border-radius: 999px;
  padding: 6px 14px;
  font-weight: 700;
  margin: 18px;
}}
dragicon {{ background: none; box-shadow: none; border: none; }}
.drag-pill {{
  background: {c['primary']};
  color: {c['on_primary']};
  border-radius: 999px;
  padding: 6px 12px;
  font-weight: 700;
}}
"""


# --- store ------------------------------------------------------------------

def read_store(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [i["path"] for i in items if isinstance(i, dict) and isinstance(i.get("path"), str)]


def pretty_dir(path):
    d = os.path.dirname(path)
    if d == HOME:
        return "~"
    if d.startswith(HOME + "/"):
        return "~" + d[len(HOME):]
    return d


def square_crop(pix):
    w, h = pix.get_width(), pix.get_height()
    side = min(w, h)
    return pix.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)


def pixbuf_texture(pix):
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pix.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pix.get_width(), pix.get_height(), fmt,
                                 pix.read_pixel_bytes(), pix.get_rowstride())


def cursor_location():
    """Pointer position relative to the output under it, or None.

    Wayland does not let clients read the global pointer position, so this asks
    the compositor. Only Hyprland is supported; elsewhere the window falls back
    to the centre of the screen.
    """
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return None
    try:
        pos = json.loads(subprocess.run(["hyprctl", "cursorpos", "-j"], capture_output=True,
                                        text=True, timeout=1).stdout)
        monitors = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                                             text=True, timeout=1).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    for m in monitors:
        scale = m.get("scale") or 1
        width, height = m["width"] / scale, m["height"] / scale
        if m.get("transform", 0) % 2:
            width, height = height, width
        if m["x"] <= pos["x"] < m["x"] + width and m["y"] <= pos["y"] < m["y"] + height:
            return {
                "monitor": m["name"],
                "x": pos["x"] - m["x"],
                "y": pos["y"] - m["y"],
                "width": width,
                "height": height,
                "reserved": m.get("reserved") or [0, 0, 0, 0],  # left, top, right, bottom
            }
    return None


def file_list(paths):
    return Gdk.FileList.new_from_list([Gio.File.new_for_path(p) for p in paths])


# --- rows -------------------------------------------------------------------

class ShelfRow(Gtk.ListBoxRow):
    def __init__(self, window, path):
        super().__init__()
        self.path = path
        self.window = window
        exists = os.path.exists(path)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.set_child(box)
        if not exists:
            self.add_css_class("row-missing")

        box.append(self._thumb(path, exists))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=os.path.basename(path.rstrip("/")) or path, xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.add_css_class("row-name")
        meta = Gtk.Label(label=self._meta(path, exists), xalign=0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.add_css_class("row-meta")
        text.append(name)
        text.append(meta)
        box.append(text)

        remove = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("shelf-icon")
        remove.add_css_class("danger")
        remove.add_css_class("row-remove")
        remove.set_tooltip_text("Remove from shelf")
        remove.connect("clicked", lambda *_: window.remove_paths([self.path]))
        box.append(remove)

        self.set_tooltip_text(path)

        if exists:
            src = Gtk.DragSource(actions=Gdk.DragAction.COPY)
            src.connect("prepare", self._on_prepare)
            src.connect("drag-begin", self._on_drag_begin)
            src.connect("drag-cancel", self._on_drag_cancel)
            src.connect("drag-end", self._on_drag_end)
            self.add_controller(src)
        self._drag_paths = []
        self._cancelled = False

    @staticmethod
    def _meta(path, exists):
        if not exists:
            return "Missing · " + pretty_dir(path)
        try:
            if os.path.isdir(path):
                n = len(os.listdir(path))
                size = f"Folder · {n} item" + ("" if n == 1 else "s")
            else:
                size = GLib.format_size(os.path.getsize(path))
        except OSError:
            size = "—"
        return f"{size} · {pretty_dir(path)}"

    def _thumb(self, path, exists):
        frame = Gtk.Box(width_request=THUMB, height_request=THUMB, valign=Gtk.Align.CENTER)
        frame.set_hexpand(False)  # stop the centred child's hexpand from widening the row slot
        frame.add_css_class("row-thumb")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        if not exists:
            icon = Gtk.Image(icon_name="dialog-warning-symbolic", pixel_size=20, hexpand=True, halign=Gtk.Align.CENTER)
            frame.append(icon)
            return frame
        gfile = Gio.File.new_for_path(path)
        try:
            info = gfile.query_info("standard::symbolic-icon,standard::content-type,thumbnail::path,thumbnail::is-valid",
                                    Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            info = None
        texture = None
        if info is not None:
            thumb = info.get_attribute_byte_string("thumbnail::path")
            ctype = info.get_content_type() or ""
            source = thumb if thumb and info.get_attribute_boolean("thumbnail::is-valid") else None
            if source is None and ctype.startswith("image/") and ctype != "image/svg+xml":
                source = path
            if source is not None:
                try:
                    texture = pixbuf_texture(square_crop(
                        GdkPixbuf.Pixbuf.new_from_file_at_scale(source, THUMB * 4, THUMB * 4, True)))
                except GLib.Error:
                    texture = None
        if texture is not None:
            pic = Gtk.Image.new_from_paintable(texture)
            pic.set_pixel_size(THUMB)
            frame.append(pic)
        else:
            gicon = info.get_symbolic_icon() if info is not None else None
            image = Gtk.Image.new_from_gicon(gicon) if gicon else Gtk.Image(icon_name="text-x-generic-symbolic")
            image.add_css_class("row-thumb-icon")
            image.set_pixel_size(20)
            image.set_hexpand(True)
            image.set_halign(Gtk.Align.CENTER)
            frame.append(image)
        return frame

    def _on_prepare(self, source, x, y):
        paths = self.window.selected_paths()
        if self.path not in paths or len(paths) < 2:
            paths = [self.path]
        self._drag_paths = [p for p in paths if os.path.exists(p)]
        if not self._drag_paths:
            return None
        self.window.dragging_out = True
        return Gdk.ContentProvider.new_for_value(file_list(self._drag_paths))

    def _on_drag_begin(self, source, drag):
        self._cancelled = False
        Gtk.DragIcon.get_for_drag(drag).set_child(self.window.drag_pill(self._drag_paths))

    def _on_drag_cancel(self, source, drag, reason):
        self._cancelled = True
        return False

    def _on_drag_end(self, source, drag, delete_data):
        self.window.dragging_out = False
        if not self._cancelled:
            self.window.after_drag_out(self._drag_paths)


# --- window -----------------------------------------------------------------

class ShelfWindow(Gtk.ApplicationWindow):
    def __init__(self, app, opts):
        super().__init__(application=app, title="Shelf")
        self.opts = opts
        self.paths = []
        self.dragging_out = False
        self.add_css_class("shelf-window")
        self.set_default_size(340, -1)
        self.set_resizable(False)
        self.set_decorated(False)

        if LayerShell is not None:
            self._setup_layer_shell(opts.position)

        overlay = Gtk.Overlay()
        self.set_child(overlay)

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.card.add_css_class("shelf-card")
        self.card.set_size_request(316, -1)
        overlay.set_child(self.card)

        self.card.append(self._header())

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False,
                               interpolate_size=True)
        self.stack.add_named(self._empty_state(), "empty")

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        self.listbox.add_css_class("shelf-list")
        self.listbox.set_activate_on_single_click(False)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.connect("selected-rows-changed", lambda *_: self._update_header())
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(380)
        scroller.set_child(self.listbox)
        self.stack.add_named(scroller, "list")
        self.card.append(self.stack)

        self.hint = Gtk.Label(xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        self.hint.add_css_class("shelf-hint")
        self.card.append(self.hint)

        self.banner = Gtk.Label(label="Drop to add to shelf", halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        self.banner.add_css_class("drop-banner")
        self.banner.set_visible(False)
        self.banner.set_can_target(False)
        overlay.add_overlay(self.banner)

        # Async target: file managers such as Nautilus often offer only MOVE,
        # which a COPY-only Gtk.DropTarget refuses. Accept any action, read the
        # file list ourselves, and always finish the drop as COPY so the source
        # never deletes the file.
        drop = Gtk.DropTargetAsync.new(None, Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK)
        drop.connect("accept", self._on_drop_accept)
        drop.connect("drag-enter", self._on_drop_enter)
        drop.connect("drag-motion", lambda *_: Gdk.DragAction.COPY)
        drop.connect("drag-leave", self._on_drop_leave)
        drop.connect("drop", self._on_drop)
        overlay.add_controller(drop)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.reload()
        self._monitor = Gio.File.new_for_path(opts.store).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", lambda *_: GLib.timeout_add(60, self._reload_once))
        self._reload_pending = False

    # layer shell

    def _setup_layer_shell(self, position):
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "noctalia-shelf")
        LayerShell.set_layer(self, LayerShell.Layer.TOP)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        self._cursor = cursor_location() if position == "cursor" else None
        if self._cursor is not None:
            self._place_at_cursor()
            # The real size is known only once the window is laid out.
            self.connect("map", lambda *_: GLib.idle_add(self._place_at_cursor))
            return
        edges = {
            "right": [LayerShell.Edge.RIGHT],
            "left": [LayerShell.Edge.LEFT],
            "top": [LayerShell.Edge.TOP],
            "bottom": [LayerShell.Edge.BOTTOM],
            "top_right": [LayerShell.Edge.TOP, LayerShell.Edge.RIGHT],
            "top_left": [LayerShell.Edge.TOP, LayerShell.Edge.LEFT],
            "bottom_right": [LayerShell.Edge.BOTTOM, LayerShell.Edge.RIGHT],
            "bottom_left": [LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT],
        }.get(position, [])  # "center", or "cursor" with no pointer position
        for edge in edges:
            LayerShell.set_anchor(self, edge, True)
            LayerShell.set_margin(self, edge, 8)

    def follow_cursor(self):
        """Move an already open window to the pointer, when placed by cursor."""
        if LayerShell is None or self.opts.position != "cursor":
            return
        loc = cursor_location()
        if loc is not None:
            self._cursor = loc
            self._place_at_cursor()

    def _place_at_cursor(self):
        loc = getattr(self, "_cursor", None)
        if LayerShell is None or loc is None:
            return False
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, False)
        LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
        # Measure margins from the output edge, not from the bar's reserved area.
        LayerShell.set_exclusive_zone(self, -1)
        monitors = self.get_display().get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            if monitor.get_connector() == loc["monitor"]:
                LayerShell.set_monitor(self, monitor)
                break
        width = self.get_width() or 340
        height = self.get_height() or 220
        res_left, res_top, res_right, res_bottom = (list(loc["reserved"]) + [0, 0, 0, 0])[:4]

        def clamp(value, low, high):
            return int(max(low, min(value, max(low, high))))

        # Centre the window on the pointer, kept inside the free area.
        left = clamp(loc["x"] - width / 2, res_left, loc["width"] - res_right - width)
        top = clamp(loc["y"] - height / 2, res_top, loc["height"] - res_bottom - height)
        LayerShell.set_margin(self, LayerShell.Edge.LEFT, left)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, top)
        return False

    # header / empty state

    def _header(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Shelf", xalign=0)
        title.add_css_class("shelf-title")
        row.append(title)
        self.count = Gtk.Label()
        self.count.add_css_class("shelf-count")
        self.count.set_valign(Gtk.Align.CENTER)
        row.append(self.count)
        row.append(Gtk.Box(hexpand=True))

        # "Drag all" pill: a drag source, not a button, so a press-and-drag
        # carries every file (or the selection) out in one go.
        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        pill.add_css_class("shelf-drag-all")
        pill.append(Gtk.Image(icon_name="drag-handle-symbolic", pixel_size=14))
        self.drag_all_label = Gtk.Label(label="Drag all")
        pill.append(self.drag_all_label)
        pill.set_cursor(Gdk.Cursor.new_from_name("grab", None))
        pill.set_tooltip_text("Drag every file on the shelf into another app")
        src = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        src.connect("prepare", self._on_all_prepare)
        src.connect("drag-begin", self._on_all_begin)
        src.connect("drag-cancel", self._on_all_cancel)
        src.connect("drag-end", self._on_all_end)
        pill.add_controller(src)
        self.drag_all = pill
        self._all_paths = []
        self._all_cancelled = False
        row.append(pill)

        self.clear_btn = Gtk.Button(icon_name="edit-delete-symbolic")
        self.clear_btn.add_css_class("shelf-icon")
        self.clear_btn.add_css_class("danger")
        self.clear_btn.set_tooltip_text("Clear shelf")
        self.clear_btn.connect("clicked", lambda *_: self.clear())
        row.append(self.clear_btn)

        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("shelf-icon")
        close.set_tooltip_text("Close (Esc)")
        close.connect("clicked", lambda *_: self.close())
        row.append(close)
        return row

    def _empty_state(self):
        zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        zone.add_css_class("shelf-dropzone")
        icon = Gtk.Image(icon_name="folder-download-symbolic", pixel_size=36)
        icon.add_css_class("shelf-dropzone-icon")
        zone.append(icon)
        title = Gtk.Label(label="Drop files here")
        title.add_css_class("shelf-dropzone-title")
        zone.append(title)
        sub = Gtk.Label(label="Files wait here until you drag them out.\nCtrl+V pastes copied files.",
                        justify=Gtk.Justification.CENTER, wrap=True)
        sub.add_css_class("shelf-sub")
        zone.append(sub)
        return zone

    # data

    def _reload_once(self):
        self.reload()
        return False

    def reload(self):
        paths = read_store(self.opts.store)
        if paths is None:
            paths = [] if not os.path.exists(self.opts.store) else self.paths
        selected = set(self.selected_paths())
        if paths != self.paths:
            self.paths = paths
            child = self.listbox.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.listbox.remove(child)
                child = nxt
            for p in paths:
                row = ShelfRow(self, p)
                self.listbox.append(row)
                if p in selected:
                    self.listbox.select_row(row)
        self.stack.set_visible_child_name("list" if self.paths else "empty")
        self._update_header()
        if getattr(self, "_cursor", None) is not None:
            GLib.timeout_add(80, self._place_at_cursor)

    def _update_header(self):
        n = len(self.paths)
        self.count.set_label(str(n))
        self.count.set_visible(n > 0)
        sel = len(self.selected_paths())
        self.drag_all.set_visible(n > 0)
        self.drag_all_label.set_label(f"Drag {sel}" if sel > 1 else "Drag all")
        self.clear_btn.set_visible(n > 0)
        if n == 0:
            self.hint.set_visible(False)
        else:
            self.hint.set_visible(True)
            self.hint.set_label("Drag to any app · Ctrl-click to pick several")

    def selected_paths(self):
        return [r.path for r in self.listbox.get_selected_rows()] if hasattr(self, "listbox") else []

    def add_paths(self, paths):
        paths = [p for p in paths if p]
        if paths:
            emit("add", paths=paths)

    def remove_paths(self, paths):
        if paths:
            emit("remove", paths=paths)

    def clear(self):
        emit("clear")

    def after_drag_out(self, paths):
        if self.opts.remove_after_drag:
            self.remove_paths(paths)
        if self.opts.close_after_drag:
            self.close()

    def drag_pill(self, paths):
        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pill.add_css_class("drag-pill")
        pill.append(Gtk.Image(icon_name="emblem-documents-symbolic", pixel_size=14))
        label = os.path.basename(paths[0].rstrip("/")) if len(paths) == 1 else f"{len(paths)} files"
        pill.append(Gtk.Label(label=label))
        return pill

    # drag all

    def _on_all_prepare(self, source, x, y):
        sel = self.selected_paths()
        paths = sel if len(sel) > 1 else self.paths
        self._all_paths = [p for p in paths if os.path.exists(p)]
        if not self._all_paths:
            return None
        self.dragging_out = True
        return Gdk.ContentProvider.new_for_value(file_list(self._all_paths))

    def _on_all_begin(self, source, drag):
        self._all_cancelled = False
        Gtk.DragIcon.get_for_drag(drag).set_child(self.drag_pill(self._all_paths))

    def _on_all_cancel(self, source, drag, reason):
        self._all_cancelled = True
        return False

    def _on_all_end(self, source, drag, delete_data):
        self.dragging_out = False
        if not self._all_cancelled:
            self.after_drag_out(self._all_paths)

    # drop in

    def _on_drop_accept(self, target, drop):
        # Ignore our own drags so dragging a row across the window is a no-op.
        if self.dragging_out:
            return False
        # Other apps offer MIME types, not the GdkFileList GType, so check
        # both. GTK deserializes either into a GdkFileList for the drop handler.
        formats = drop.get_formats()
        return (formats.contain_gtype(Gdk.FileList)
                or formats.contain_mime_type("text/uri-list")
                or formats.contain_mime_type("application/vnd.portal.filetransfer"))

    def _on_drop_enter(self, target, drop, x, y):
        self.card.add_css_class("drop-hover")
        self.banner.set_visible(bool(self.paths))
        return Gdk.DragAction.COPY

    def _on_drop_leave(self, target, drop=None):
        self.card.remove_css_class("drop-hover")
        self.banner.set_visible(False)

    def _on_drop(self, target, drop, x, y):
        self._on_drop_leave(target)

        def done(source, result):
            try:
                value = source.read_value_finish(result)
            except GLib.Error as err:
                print(f"shelf: could not read dropped files: {err.message}", file=sys.stderr)
                source.finish(0)
                return
            self.add_paths([f.get_path() for f in value.get_files() if f.get_path()])
            source.finish(Gdk.DragAction.COPY)

        drop.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, done)
        return True

    # misc input

    def _on_row_activated(self, listbox, row):
        if os.path.exists(row.path):
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(row.path).get_uri(), None)
            except GLib.Error as err:
                print(f"shelf: cannot open {row.path}: {err.message}", file=sys.stderr)

    def _on_key(self, ctrl, keyval, keycode, state):
        ctrl_down = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.selected_paths():
            self.remove_paths(self.selected_paths())
            return True
        if ctrl_down and keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.listbox.select_all()
            return True
        if ctrl_down and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self._paste()
            return True
        return False

    def _paste(self):
        clipboard = self.get_display().get_clipboard()

        def done(cb, result):
            try:
                value = cb.read_value_finish(result)
            except GLib.Error:
                cb.read_text_async(None, text_done)
                return
            if value is not None:
                self.add_paths([f.get_path() for f in value.get_files() if f.get_path()])

        def text_done(cb, result):
            try:
                text = cb.read_text_finish(result) or ""
            except GLib.Error:
                return
            paths = []
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("file://"):
                    line = Gio.File.new_for_uri(line).get_path() or ""
                if line.startswith("/") and os.path.exists(line):
                    paths.append(line)
            self.add_paths(paths)

        clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, done)


class ShelfApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_command_line(self, command_line):
        opts = parse_args(command_line.get_arguments()[1:])
        if self.window is not None:
            if opts.toggle:
                self.window.close()
            else:
                self.window.follow_cursor()
                self.window.present()
            return 0
        if opts.toggle and command_line.get_is_remote():
            return 0
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_string(build_css(load_colors(opts.colors)))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.window = ShelfWindow(self, opts)
        self.window.connect("close-request", self._on_close)
        self.window.present()
        emit("ready", layer_shell=LayerShell is not None)
        return 0

    def _on_close(self, window):
        emit("bye")
        self.window = None
        return False


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="shelf-window")
    parser.add_argument("--store", required=True, help="path of the plugin's shelf.json")
    parser.add_argument("--position", default="cursor",
                        choices=["cursor", "right", "left", "top", "bottom", "top_right", "top_left",
                                 "bottom_right", "bottom_left", "center"])
    parser.add_argument("--colors", type=json.loads, default={}, help="JSON map of palette role to #rrggbb")
    parser.add_argument("--remove-after-drag", action="store_true")
    parser.add_argument("--close-after-drag", action="store_true")
    parser.add_argument("--toggle", action="store_true", help="close the running window instead of raising it")
    return parser.parse_args(argv)


def main():
    parse_args(sys.argv[1:])  # fail fast on bad arguments, before GTK starts
    app = ShelfApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

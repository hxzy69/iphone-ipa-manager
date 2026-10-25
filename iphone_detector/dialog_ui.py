"""Shared, resizeable dialog layout and desktop visual defaults."""

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

BACKGROUND = "#101820"
PANEL = "#19252f"
TEXT = "#edf3f7"
MUTED = "#9bafbd"


def size_window(window, width, height, minimum=(560, 420)):
    """Scale the initial/minimum size with text while staying on the display."""
    factor = max(1.0, float(window.tk.call("tk", "scaling")) / (96 / 72))
    available_width = max(320, window.winfo_screenwidth() - 60)
    available_height = max(300, window.winfo_screenheight() - 100)
    width = min(round(width * factor), available_width)
    height = min(round(height * factor), available_height)
    window.geometry(f"{width}x{height}")
    window.minsize(min(round(minimum[0] * factor), width), min(round(minimum[1] * factor), height))


def reveal_focus(canvas, body, widget):
    """Keep keyboard navigation within a long form visible without moving focus."""
    ancestor = widget
    while ancestor is not None and ancestor is not body:
        ancestor = getattr(ancestor, "master", None)
    bounds = canvas.bbox("all")
    if ancestor is None or not bounds or bounds[3] <= canvas.winfo_height():
        return
    top = widget.winfo_rooty() - canvas.winfo_rooty()
    bottom = top + widget.winfo_height()
    current = canvas.canvasy(0)
    if top < 0:
        canvas.yview_moveto(max(0, current + top - 8) / bounds[3])
    elif bottom > canvas.winfo_height():
        canvas.yview_moveto((current + bottom - canvas.winfo_height() + 8) / bounds[3])


def scrollbar_callback(scroll, content, side="right"):
    """Hide an idle scrollbar, preserving its packing order when it is needed."""

    def update(first, last):
        scroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            scroll.pack_forget()
        elif not scroll.winfo_manager():
            options = {"side": side, "fill": "x" if side == "bottom" else "y"}
            if content.winfo_manager() == "pack":
                options["before"] = content
            scroll.pack(**options)

    return update


def style_window(window):
    window.configure(bg=BACKGROUND)
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 10), background=BACKGROUND, foreground=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("TEntry", fieldbackground="#243541", foreground=TEXT, padding=7)
    style.configure("TCombobox", fieldbackground="#243541", foreground=TEXT, padding=6)
    style.map(
        "TCombobox", fieldbackground=[("readonly", "#243541")], foreground=[("readonly", TEXT)]
    )
    style.configure("TButton", padding=(13, 8), background="#2c414f", foreground=TEXT)
    style.configure(
        "Accent.TButton", background="#49d8bd", foreground="#102620", font=("Segoe UI", 10, "bold")
    )
    style.map("Accent.TButton", background=[("active", "#78e8d2"), ("disabled", "#263d40")])
    style.configure(
        "Horizontal.TProgressbar", background="#49d8bd", troughcolor=PANEL, bordercolor=PANEL
    )
    style.configure(
        "Vertical.TScrollbar",
        background="#2c414f",
        troughcolor=BACKGROUND,
        bordercolor=BACKGROUND,
        arrowcolor=MUTED,
    )
    style.map("TButton", background=[("active", "#3b5565")], foreground=[("disabled", MUTED)])
    style.configure("TCheckbutton", background=BACKGROUND, foreground=TEXT)
    style.configure("TRadiobutton", background=BACKGROUND, foreground=TEXT)
    style.configure(
        "Treeview",
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        rowheight=max(28, tkfont.Font(window, font=("Segoe UI", 10)).metrics("linespace") + 10),
    )
    style.configure("Treeview.Heading", background="#243541", foreground=TEXT)
    window.option_add("*Listbox.background", PANEL)
    window.option_add("*Listbox.foreground", TEXT)
    window.option_add("*Listbox.selectBackground", "#2c414f")


def shell(window, title, description="", *, width=760, height=650, close=None, scrollable=True):
    """Keep the title and close control visible while long forms scroll safely."""
    pending = set()
    original_after = window.after

    def after(delay, callback=None, *args):
        if callback is None:
            return original_after(delay)
        identifier = None

        def run():
            pending.discard(identifier)
            callback(*args)

        identifier = original_after(delay, run)
        pending.add(identifier)
        return identifier

    def cancel_timers(event):
        if event.widget is window:
            for identifier in tuple(pending):
                try:
                    window.after_cancel(identifier)
                except tk.TclError:
                    pass
            pending.clear()

    window.after = after
    window.bind("<Destroy>", cancel_timers, add="+")
    window.title(title)
    window.configure(bg=BACKGROUND)
    size_window(window, width, height)
    window.bind("<Escape>", lambda event: (close or window.destroy)())
    header = ttk.Frame(window, padding=(20, 16, 20, 10))
    header.pack(fill="x")
    ttk.Label(header, text=title, font=("Segoe UI", 18, "bold")).pack(anchor="w")
    if description:
        label = ttk.Label(header, text=description, style="Muted.TLabel", wraplength=width - 60)
        label.pack(fill="x", pady=(6, 0))
    footer = ttk.Frame(window, padding=(20, 10))
    footer.pack(side="bottom", fill="x")
    ttk.Button(footer, text="Close", command=close or window.destroy).pack(side="right")

    def fit_label(event):
        if isinstance(event.widget, ttk.Label) and event.width > 1:
            # A label's own requested width depends on its wrap length. Feeding
            # that width back can alternate forever at a word boundary.
            desired = max(120, event.widget.master.winfo_width() - 40)
            if str(event.widget.cget("wraplength")) != str(desired):
                event.widget.configure(wraplength=desired)

    window.bind("<Configure>", fit_label, add="+")

    if not scrollable:
        body = ttk.Frame(window, padding=(20, 6, 20, 14))
        body.pack(fill="both", expand=True)
        return body, footer
    host = ttk.Frame(window)
    host.pack(fill="both", expand=True)
    canvas = tk.Canvas(host, bg=BACKGROUND, highlightthickness=0)
    scroll = ttk.Scrollbar(host, command=canvas.yview)

    canvas.configure(yscrollcommand=scrollbar_callback(scroll, canvas))
    scroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    body = ttk.Frame(canvas, padding=(20, 6, 20, 14))
    item = canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))

    def wheel(event):
        widget = event.widget
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview, ttk.Combobox)):
            return
        while widget is not None:
            if widget is body:
                bounds = canvas.bbox("all")
                if bounds and bounds[3] > canvas.winfo_height():
                    units = int(event.delta / 120) or (
                        1 if event.delta > 0 else -1 if event.delta < 0 else 0
                    )
                    canvas.yview_scroll(-units, "units")
                return
            widget = getattr(widget, "master", None)

    window.bind("<MouseWheel>", wheel, add="+")
    window.bind("<FocusIn>", lambda event: reveal_focus(canvas, body, event.widget), add="+")

    return body, footer


def section(parent, title, description=""):
    ttk.Label(parent, text=title, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 5))
    if description:
        label = ttk.Label(parent, text=description, style="Muted.TLabel", wraplength=600)
        label.pack(fill="x", pady=(0, 8))


def preview_notice(parent):
    from tkinter import messagebox

    messagebox.showinfo(
        "Preview only", "Sample data only. Nothing was saved, sent, or installed.", parent=parent
    )


def wrap_actions(frame):
    """Reflow a group of controls into rows instead of clipping narrow windows."""
    children = frame.winfo_children()
    for child in children:
        child.pack_forget()
    previous = [None]

    def layout(event):
        required = max((child.winfo_reqwidth() for child in children), default=1) + 12
        columns = max(1, min(len(children), event.width // required))
        if previous[0] == columns:
            return
        for column in range(len(children)):
            frame.columnconfigure(
                column,
                weight=1 if column < columns else 0,
                uniform="actions" if column < columns else "",
            )
        for index, child in enumerate(children):
            child.grid(row=index // columns, column=index % columns, sticky="ew", padx=4, pady=4)
        previous[0] = columns

    frame.bind("<Configure>", layout, add="+")

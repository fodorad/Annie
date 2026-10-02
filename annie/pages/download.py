"""Save a generated document to the *user's* machine, with their choice of folder and name.

Annie's server runs where the data is — often inside a Docker container — so a server-side
folder picker would browse the wrong filesystem. The browser is where the person is, so the
save happens there: Chromium browsers (Chrome, Edge, Brave, ...) open the operating system's
native *Save as* dialog through the File System Access API, starting in the user's Documents
folder with the suggested name prefilled. Browsers without it (Firefox, Safari) fall back to a
normal download using the suggested name. The content is generated on the server, fetched by
the browser from a small route, and written to disk there — server → browser → disk — so local
runs and Docker behave identically.
"""

from __future__ import annotations

import json
import re

from nicegui import ui

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
"""Characters no major filesystem allows in a file name (path separators included)."""

_MAX_STEM = 120
"""Longest accepted file-name stem, comfortably below every filesystem's limit."""

MIME_TYPES: dict[str, str] = {"json": "application/json", "csv": "text/csv"}
"""Extension → MIME type for the formats Annie exports."""


def sanitize_filename(name: str, *, extension: str, default_stem: str) -> str:
    """Make ``name`` a safe file name that ends in ``.extension``.

    Path separators and characters illegal on Windows are replaced, surrounding dots and
    spaces trimmed, an empty result falls back to ``default_stem``, and the extension is
    forced (added if missing, never doubled).

    Args:
        name: The name the user typed (may be empty or contain a path).
        extension: The required extension without the dot, e.g. ``"csv"``.
        default_stem: Stem to use when ``name`` has nothing usable.

    Returns:
        A file name such as ``annie_events_clip1.csv``.
    """
    suffix = f".{extension}"
    stem = _ILLEGAL.sub("_", name.strip())
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    stem = stem.strip(" .")[:_MAX_STEM].rstrip(" .")
    return f"{stem or default_stem}{suffix}"


def save_from_url_js(filename: str, url: str, mime: str) -> str:
    """Return a click handler that fetches ``url`` and saves it where the user picks.

    The handler runs client-side *inside the click*: it fetches the text first (a local,
    millisecond request, well inside the browser's transient-activation window), then opens
    the native Save dialog with ``filename`` prefilled and writes the file. A ``204``
    response means "nothing to export" and shows a notice instead of an empty file.
    Cancelling the dialog is silent. Browsers without ``showSaveFilePicker`` (Firefox,
    Safari) fall back to a normal download under ``filename``.

    Args:
        filename: Suggested file name, already sanitised.
        url: Same-origin URL returning the file's text content.
        mime: MIME type, e.g. ``"text/csv"``.

    Returns:
        A JavaScript arrow function (as a string) for ``Element.on(..., js_handler=...)``.
    """
    name_js = json.dumps(filename)
    url_js = json.dumps(url)
    mime_js = json.dumps(mime)
    ext_js = json.dumps(f".{filename.rsplit('.', 1)[-1]}")
    return f"""async () => {{
      const name = {name_js}, mime = {mime_js}, ext = {ext_js};
      const response = await fetch({url_js});
      if (response.status === 204) {{
        Quasar.Notify.create({{message: 'Nothing to export yet.', color: 'warning'}});
        return;
      }}
      const blob = new Blob([await response.text()], {{type: mime}});
      if (window.showSaveFilePicker) {{
        try {{
          const handle = await window.showSaveFilePicker({{
            suggestedName: name,
            startIn: 'documents',
            types: [{{description: name, accept: {{[mime]: [ext]}}}}],
          }});
          const out = await handle.createWritable();
          await out.write(blob);
          await out.close();
        }} catch (err) {{
          if (err && err.name !== 'AbortError') throw err;
        }}
        return;
      }}
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }}"""


def attach_save(element: ui.element, *, filename: str, url: str, extension: str) -> None:
    """Make clicking ``element`` open the native Save dialog straight away.

    Args:
        element: A button or menu item.
        filename: Default file name offered in the Save dialog.
        url: Same-origin URL serving the file's text (see :func:`save_from_url_js`).
        extension: File extension without the dot (selects the MIME type).
    """
    element.on(
        "click",
        js_handler=save_from_url_js(filename, url, MIME_TYPES.get(extension, "text/plain")),
    )


def export_menu_item(label: str, *, filename: str, url: str, extension: str) -> ui.menu_item:
    """Add a menu entry that opens the native Save dialog straight away when clicked.

    Args:
        label: Menu text.
        filename: Default file name offered in the Save dialog.
        url: Same-origin URL serving the file's text (see :func:`save_from_url_js`).
        extension: File extension without the dot (selects the MIME type).

    Returns:
        The created menu item.
    """
    item = ui.menu_item(label)
    attach_save(item, filename=filename, url=url, extension=extension)
    return item

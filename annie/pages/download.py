"""Save a generated document to the *user's* machine, with their choice of folder and name.

Annie's server runs where the data is — often inside a Docker container — so a server-side
folder picker would browse the wrong filesystem. The browser is where the person is, so the
save happens there: Chromium browsers (Chrome, Edge, Brave, ...) open the operating system's
native *Save as* dialog through the File System Access API, starting in the user's Documents
folder with the suggested name prefilled. Browsers without it (Firefox, Safari) fall back to a
normal download using the chosen name. The content is generated on the server and travels
server → browser → disk, so local runs and Docker behave identically.
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


def save_script(filename: str, text: str, mime: str, *, name_input_id: str | None = None) -> str:
    """Return JavaScript that saves ``text`` as ``filename`` on the user's machine.

    It must run inside the click that triggered the save (browsers only open a file picker
    during a user gesture), so attach it with a client-side ``js_handler`` rather than
    sending it from the server afterwards. Cancelling the picker is silent.

    Args:
        filename: Suggested file name, already sanitised.
        text: The file's full text content.
        mime: MIME type, e.g. ``"text/csv"``.
        name_input_id: DOM id of a text input whose current value overrides ``filename`` (the
            dialog's editable name box). It is cleaned the same way :func:`sanitize_filename`
            does; an empty box falls back to ``filename``.

    Returns:
        A JavaScript expression (an async arrow function body wrapped in an IIFE).
    """
    # json.dumps yields valid, fully escaped JS string literals; "</" is escaped too so the
    # text can never close a surrounding <script> element.
    name_js = json.dumps(filename)
    text_js = json.dumps(text).replace("</", "<\\/")
    mime_js = json.dumps(mime)
    ext = f".{filename.rsplit('.', 1)[-1]}"
    ext_js = json.dumps(ext)
    input_js = json.dumps(name_input_id)
    return f"""
    (async () => {{
      const text = {text_js}, mime = {mime_js}, ext = {ext_js};
      let name = {name_js};
      const box = {input_js} ? document.getElementById({input_js}) : null;
      if (box && box.value.trim()) {{
        let stem = box.value.trim().replace(/[\\\\/:*?"<>|\\x00-\\x1f]/g, '_');
        if (stem.toLowerCase().endsWith(ext)) stem = stem.slice(0, -ext.length);
        stem = stem.replace(/^[ .]+|[ .]+$/g, '').slice(0, {_MAX_STEM});
        if (stem) name = stem + ext;
      }}
      const blob = new Blob([text], {{type: mime}});
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
    }})();
    """


def open_save_dialog(*, title: str, filename: str, text: str, extension: str) -> None:
    """Open a dialog to confirm the file name, then save through the browser's Save dialog.

    The name box is prefilled with ``filename``. *Save…* runs :func:`save_script` as a
    client-side click handler (inside the user gesture, as browsers require), so the native
    folder/name picker opens on the user's machine, starting in Documents.

    Args:
        title: Dialog heading, e.g. ``"Export events (CSV)"``.
        filename: Default file name shown in the box.
        text: The file content, already rendered.
        extension: File extension without the dot (selects the MIME type).
    """
    input_id = "annie-save-name"
    script = save_script(
        filename, text, MIME_TYPES.get(extension, "text/plain"), name_input_id=input_id
    )
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(title).classes("text-base font-medium")
        ui.input("File name", value=filename).props(f"input-id={input_id}").classes("w-full")
        ui.label(
            "Save… opens your system's Save dialog, starting in your Documents folder, "
            "where you can pick the folder and change the name."
        ).classes("text-xs text-gray-600")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            save = ui.button("Save…", icon="save", on_click=dialog.close)
            save.on("click", js_handler=f"() => {{ {script} }}")
    dialog.open()

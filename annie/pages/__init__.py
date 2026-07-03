"""NiceGUI page builders for Annie's four tabs.

Each module exposes a ``render(...)`` function that populates the body of one tab.
Pages call **down** into the service layer only; they never import ``sqlite3`` or
``torchcodec`` directly.
"""

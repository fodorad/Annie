---
name: Bug report
about: Report something that is broken or not working as expected
labels: bug
assignees: fodorad
---

## Description

A clear and concise description of the bug.

## Steps to reproduce

```python
# Minimal reproducible example
from annie.scanning import scan_dataset

manifest = scan_dataset(videos_dir="...", annotations_dir="...")
# ...
```

## Expected behaviour

What you expected to happen.

## Actual behaviour

What actually happened. Include the full traceback if applicable.

```
Traceback (most recent call last):
  ...
```

## Environment

- Annie version: <!-- e.g. 0.1.0 — run `pip show annie` -->
- Python version: <!-- e.g. 3.12.3 -->
- FFmpeg version: <!-- e.g. 7.1 — run `ffmpeg -version | head -1` -->
- OS: <!-- e.g. Ubuntu 22.04 / macOS 15 / Windows 11 -->

## Additional context

Any other information that might be helpful (dataset layout, file names, etc.).

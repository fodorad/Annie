---
name: Feature request
about: Suggest a new feature or improvement
labels: enhancement
assignees: fodorad
---

## Problem statement

A clear description of the problem or limitation you are facing.
Example: "I need to validate annotations for AVI videos, but currently..."

## Proposed solution

Describe the feature or change you would like, including any API or UI changes.

```python
# Example of what the new API might look like
from annie.scanning import scan_dataset

manifest = scan_dataset(..., extensions=[".mp4", ".avi"])  # <-- proposed argument
```

## Alternatives considered

Any alternative approaches or workarounds you have already tried.

## Additional context

Links to datasets, related issues, or other tools that do something similar.

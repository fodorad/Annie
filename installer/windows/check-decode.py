r"""Decode a video with an installed Annie and check where the Visual C++ runtime came from.

Run with the install's environment, e.g. (CI, windows-installer.yml):

    App\env\Scripts\python.exe installer\windows\check-decode.py App\python video.mp4

PyTorch, which torchcodec decodes with, needs the Visual C++ runtime (msvcp140.dll &
co.). Many PCs lack it, so the installer bundles it next to the bundled python.exe. A
machine that also has it system-wide (like a CI runner) would decode either way, so
this checks that the bundled copies are the ones Windows actually loaded.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

#: Runtime DLLs that PyTorch loads beyond Python's own vcruntime140*.dll.
BUNDLED = ("msvcp140.dll", "msvcp140_atomic_wait.dll", "vcruntime140_threads.dll")


def loaded_from(name: str) -> Path | None:
    """The file a DLL loaded into this process came from, or ``None`` if not loaded."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    handle = kernel32.GetModuleHandleW(name)
    if not handle:
        return None
    buffer = ctypes.create_unicode_buffer(1024)
    kernel32.GetModuleFileNameW(ctypes.c_void_p(handle), buffer, len(buffer))
    return Path(buffer.value)


def main(python_dir: str, video: str) -> int:
    """Decode ``video``; fail unless the runtime DLLs came from ``python_dir``."""
    from annie.media import decode

    metadata = decode.video_metadata(video)
    print(metadata)
    if metadata.num_frames <= 0:
        print("decoded no frames")
        return 1
    expected = Path(python_dir).resolve()
    failed = False
    for name in BUNDLED:
        source = loaded_from(name)
        print(f"{name:28} {source}")
        if source is None or source.resolve().parent != expected:
            failed = True
    if failed:
        print(f"the Visual C++ runtime was not loaded from {expected}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))

# Code signing policy

Free code signing for the Windows installer is provided by
[SignPath.io](https://about.signpath.io), with a certificate by
[SignPath Foundation](https://signpath.org).

## What is signed

Only `Annie-Setup.exe`, the Windows installer attached to each
[GitHub Release](https://github.com/fodorad/Annie/releases). It is built from this
repository's source by the `Windows installer` GitHub Actions workflow
(`.github/workflows/windows-installer.yml`) on GitHub-hosted runners. Release builds
are signed. Pull-request builds never are.

The installer bundles third-party binaries, each downloaded from its upstream release
and checked against a pinned SHA-256 (`installer/windows/fetch-deps.ps1`):

- [uv](https://github.com/astral-sh/uv) by Astral
- [CPython](https://github.com/astral-sh/python-build-standalone) (python-build-standalone)
- [FFmpeg](https://github.com/BtbN/FFmpeg-Builds) (GPL shared build by BtbN; licence and
  source pointers ship in the `ffmpeg` folder)

## Team roles

- Committers and reviewers: [fodorad](https://github.com/fodorad)
- Approvers (each signing request is approved by hand): [fodorad](https://github.com/fodorad)

## Privacy

This program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it.

The installer and Annie connect to the internet only to:

- download Annie and its Python dependencies from [PyPI](https://pypi.org) (during
  setup);
- check PyPI for a newer Annie at start and from the **Check for updates** menu, and
  download it.

Annie sends none of your data anywhere. It runs on your computer and serves its pages
only to your own browser (`127.0.0.1`).

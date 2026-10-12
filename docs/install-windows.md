# Installing Annie on Windows

This page is for people who want to *use* Annie on a Windows 10 or 11 laptop. You don't
need admin rights, and nothing else has to be installed first. Annie works on locked-down
PCs where only your own user folder is writable: it changes nothing else (no registry
entries, no Start menu, nothing in hidden AppData folders).

## Install (once)

1. Download **[Annie-Setup.exe](https://github.com/fodorad/Annie/releases/latest/download/Annie-Setup.exe)**.
   This link always gives you the newest installer.
2. Double-click the downloaded file.
   - Windows may show **"Windows protected your PC"**. This appears because the installer
     isn't code-signed yet. Click **More info**, then **Run anyway**.
3. **Choose locations.** One page shows where everything goes. Keep the defaults or
   change them:

   | Folder | Default | What is in it |
   | --- | --- | --- |
   | Program folder | `C:\Users\<you>\Annie\App` | Annie, its own Python, the `annie` Python environment with all dependencies, and FFmpeg |
   | Data folder | `C:\Users\<you>\Annie\Data` | Your work: reviews, saved configs, logs |

   Setup always puts an **Annie** shortcut (to start Annie) and an **Uninstall Annie**
   shortcut in the Annie folder, `C:\Users\<you>\Annie`, which holds both folders. Tick
   **Create a desktop shortcut** if you also want one on the desktop (it is unticked by
   default). Annie is not added to the Start menu.

   Tick or untick **Install the example datasets**: small versions of First Impression V2
   and CMU-MOSEI (about 50 MB) to try Annie on. They are copied to `Data\examples`. If
   you leave them out, you can download them later from the Annie icon's menu.
4. Click **Install**. Setup downloads Annie and its components (a few hundred MB, a few
   minutes; a small window shows the progress), then **Finish** starts Annie.

You choose the folders once. Updates and reinstalls keep using them.

## Daily use

- **Start:** double-click **Annie** in the Annie folder, `C:\Users\<you>\Annie` (or on
  the desktop, if you created that shortcut). Tip: pin the folder to Quick access in File
  Explorer (right-click it, **Pin to Quick access**). A small Annie window
  appears at once, with a progress bar and a list of what is happening: checking for
  updates, the packages an update changes, the download, and Annie starting. It closes
  by itself when Annie is ready. Your browser then opens Annie, and the Annie icon
  appears in the notification area at the bottom right of the taskbar. If it is
  hidden, click the **^** arrow next to the clock. If Annie cannot start, the window
  stays open and says why.
- **The Annie icon's menu** (right-click it):
  - **Open Annie** opens it in the browser again. Double-clicking the icon does the same.
  - **Open Annie folder** opens `C:\Users\<you>\Annie` in File Explorer, which holds
    both the program (`App`) and your work (`Data`). If you put those two in unrelated
    places, it opens the data folder.
  - **Download example datasets** appears only while the examples are missing. It
    downloads them (about 50 MB) into `Data\examples`. Then reload Annie in the browser.
  - **Check for updates** checks now. If there is a newer Annie, it updates and restarts.
  - **Quit Annie** stops Annie. Closing only the browser tab does not stop it.
- **Your data drives:** in Annie's folder picker, go up to `C:\` and the other drives
  (for example a USB disk) are listed there.

## Where your work is kept

Everything you produce is in the data folder (`C:\Users\<you>\Annie\Data` by default):

| Item | Location |
| --- | --- |
| Review progress (the `.db` files) | `Data\` and `Data\sessions\` |
| Saved dataset configs | `Data\configs\` |
| Logs (useful when reporting a problem) | `Data\logs\` |
| Example datasets (if installed) | `Data\examples\` |

To try Annie on the examples, open the **Dataset** tab and choose
**[Example] CMU-MOSEI Mini** or **[Example] First Impression V2 Mini**.

Annie reopens the dataset you used last, with all your progress. To back up your work,
or to move it to another computer, copy this folder while Annie is stopped.

If you used the first Annie installer, which kept work directly in
`C:\Users\<you>\Annie`, it is moved into the new data folder automatically when that
folder is inside `C:\Users\<you>\Annie` (the default `Annie\Data`). If you choose a data
folder elsewhere, your earlier work stays where it is; move it over yourself if you like.

## Updating

Annie checks for a newer version every time it starts and installs it before opening;
the start window shows the update's progress and which packages it changes. That takes
a few seconds, and without internet Annie simply starts the version you already have. You can also check any time from the icon's menu with
**Check for updates**.

Occasionally Annie shows a banner asking you to download the new installer, when the
program folder itself (Python, FFmpeg, the launcher) needs updating. When that happens,
download it from the same link and run it once. Your work and your chosen folders are
kept.

## Something is wrong

If video frames show a red broken-image icon, and a red bar at the top of Annie says
that a Windows system library is missing, your copy of Annie predates the installer
that includes the Microsoft Visual C++ runtime, which video decoding needs. Run the
newest installer once (it keeps your folders and work). Hovering over a broken-image
icon shows why that video could not be decoded, and the **Log** tab has the details.

For anything else, run the installer again. This repairs the installation: it rebuilds the program folder
and downloads Annie's components fresh. Your work in the data folder stays where it is.
If the problem remains, send the newest file from `Data\logs\` (and `App\install.log`, if
the download failed) to the person who gave you Annie.

## Uninstall

Double-click **Uninstall Annie** in the Annie folder, `C:\Users\<you>\Annie`. (Annie is
not listed in Settings → Apps, because that list needs a registry entry.) This removes
the program completely: the program folder with Annie's Python, the `annie` environment
and everything downloaded into it, and the shortcuts. At the end it asks whether to
**also delete your work**. The default answer is **No**, which keeps the data folder. If
both folders are gone, the empty `Annie` folder around them is removed too.

Annie needs no admin rights and installs nothing outside its folders: no registry
entries, no Start menu entry, nothing in hidden AppData folders, no PATH changes, no
services. (Windows itself may still note, as it does for any program you run, that the
program was started.) If you installed Annie with an older installer, which added an
entry to Settings → Apps and the Start menu, the new installer removes those when it
updates that install.

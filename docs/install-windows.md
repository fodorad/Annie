# Installing Annie on Windows

This page is for people who want to *use* Annie on a Windows 10 or 11 laptop. You don't
need admin rights, and nothing else has to be installed first.

## Install (once)

1. Download **[Annie-Setup.exe](https://github.com/fodorad/Annie/releases/latest/download/Annie-Setup.exe)**.
   This link always gives you the newest installer.
2. Double-click the downloaded file.
   - Windows may show **"Windows protected your PC"**. This appears because the installer
     isn't code-signed yet. Click **More info**, then **Run anyway**.
3. Click **Install**, then **Finish**. Annie starts on its own the first time.

The installer adds an **Annie** icon to your desktop and Start menu.

## First start

The first start downloads Annie and its components. That's a few hundred MB, so it takes
a few minutes and needs an internet connection. A black window shows the progress, and
your browser opens Annie when it's ready.

Later starts take a few seconds. Annie checks for a newer version, installs it if there
is one, and opens. Without internet it simply starts the version you already have.

## Daily use

- **Start:** double-click **Annie**. If Annie is already running, this just opens it
  in your browser again.
- **Stop:** close the black **Annie** window. Closing only the browser tab does not stop
  Annie.
- **Your data drives:** in Annie's folder picker, go up to `C:\` and the other drives
  (for example a USB disk) are listed there.

## Where your work is kept

Everything you produce is in the **Annie** folder of your user folder
(`C:\Users\<your name>\Annie`):

| Item | Location |
| --- | --- |
| Review progress (the `.db` files) | `Annie\` and `Annie\sessions\` |
| Saved dataset configs | `Annie\configs\` |
| Logs (useful when reporting a problem) | `Annie\logs\` |

Annie reopens the dataset you used last, with all your progress. To back up your work,
or to move it to another computer, copy this folder while Annie is stopped.

## Updating

Annie updates itself every time it starts, so there's nothing to do. Occasionally Annie
shows a banner asking you to download the new installer. When that happens, download it
from the same link and run it once. Your work is not touched.

## Something is wrong

Run the installer again. This repairs the installation: the next start downloads Annie
fresh. Your work in `C:\Users\<your name>\Annie` stays where it is. If the problem
remains, send the newest file from `Annie\logs\` to the person who gave you Annie.

## Uninstall

Open **Settings → Apps → Installed apps**, find **Annie**, and choose **Uninstall**.
This removes the program completely: everything it downloaded, its shortcuts, and its
entry in the Apps list. At the end it asks whether to **also delete your work**. The
default answer is **No**, which keeps the `Annie` folder in your user folder.

Annie needs no admin rights and installs nothing outside its own folder. The single
registry entry it creates is the one that makes it appear in the Apps list, and
uninstalling removes it.

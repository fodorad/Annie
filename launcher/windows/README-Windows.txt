========================================
 Annie — how to install and use (Windows)
========================================

NOTE: this is the Docker-based ALTERNATIVE, meant for IT-managed machines.
Most people should use the simpler installer instead (no Docker, no admin rights):
  https://github.com/fodorad/Annie/releases/latest/download/Annie-Setup.exe
Guide: https://fodorad.github.io/Annie/install-windows.html

Annie lets you watch your video recordings and mark events on a timeline.
You do NOT need to know anything technical. Follow these three steps.


STEP 1 — Install Docker Desktop (once)
--------------------------------------
Annie runs inside "Docker Desktop", a free program that only needs to be
installed one time on each computer.

  1. Go to:  https://www.docker.com/products/docker-desktop
  2. Download "Docker Desktop for Windows" and install it (click Next / Finish).
  3. Start Docker Desktop from the Start menu. Wait until it says "Running"
     (the little whale icon near the clock stops animating).

You only ever do this step once per computer.


STEP 2 — Unzip the Annie folder
-------------------------------
If you received Annie as a .zip file, right-click it and choose
"Extract All...". Put the folder somewhere easy, like your Desktop.


STEP 3 — Start Annie
--------------------
Double-click the file called:  Annie   (it has a gear icon; full name Annie.bat)

  * The FIRST time, a window will ask you to choose the folder that contains
    your video recordings. Browse to your external drive and select it.
    Annie remembers this choice.

  * Annie then opens automatically in your web browser. That's it — you can
    start working.

  * When you are finished, go back to the small black Annie window and press
    Enter to stop it.


Everyday use
------------
Just double-click Annie whenever you want to work. It opens your browser
automatically. Your videos never leave your computer.

If you plug your external drive into a different computer, or its drive letter
changes, Annie will simply ask you to pick the video folder again — once.

Annie updates itself: whenever a newer version is available, it is downloaded
automatically the next time you start Annie. You don't have to do anything.


Where are my exported files?
----------------------------
Anything you export (CSV or JSON) is saved inside the "annie-home" folder that
sits next to the Annie launcher, so you can find it in File Explorer.


Something went wrong?
---------------------
  * "Docker Desktop is not running" — open Docker Desktop from the Start menu,
    wait until it says Running, then start Annie again.
  * The page is blank — wait a few seconds and refresh the browser.
  * Still stuck — contact the person who gave you Annie.

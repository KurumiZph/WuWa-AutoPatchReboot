# Developing / Building From Source

This covers running `wuwa-apr.py` directly and building your own `.exe`.
If you just want to use the app, see [Readme](README.md) instead.

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [Running From Source](#running-from-source)
- [Configuration](#configuration)
- [Testing the Fallback Flows](#testing-the-fallback-flows)
- [Building the .exe](#building-the-exe)
- [Where Data Is Stored](#where-data-is-stored)
- [Important Considerations](#important-considerations)

## Requirements

- Windows 10/11
- Python 3.13+
- Tesseract-OCR (or let the script prompt you for it on first run)
- Git (to clone the repo)

## Setup

```bash
git clone https://github.com/KurumiZph/WuWa-AutoPatchReboot.git
cd WuWa-AutoPatchReboot
```

Dependencies are installed automatically the first time you run the
script (see `ensure_dependencies()` near the top of `wuwa-apr.py`),
missing packages get pip-installed on the spot. If you'd rather install
them yourself up front:

```bash
python -m pip install -r requirements.txt
```

## Running From Source

```bash
python wuwa-apr.py
```

First run will walk you through the same UAC prompt / Tesseract check /
game-detection flow described in the main README.

## Configuration

Near the top of `wuwa-apr.py`:

```python
GAME_EXE = r"C:\Path\To\Wuthering Waves.exe"   # fallback default; usually
                                                 # auto-detected or remembered
                                                 # in config.json instead
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

RESTART_WAIT_SECONDS = 15          # delay before relaunching after patch
CHECK_INTERVAL_SECONDS = 1.0       # OCR scan frequency

LOGIN_CONFIRMATIONS_REQUIRED = 2   # consecutive hits needed to confirm
PATCH_CONFIRMATIONS_REQUIRED = 2

LOGIN_MATCH_THRESHOLD = 0.72       # fuzzy-match thresholds (1.0 = exact)
PATCH_MATCH_THRESHOLD = 0.68

PATCH_CENTER_X_RANGE = (0.15, 0.85)  # popup must appear roughly centered
PATCH_CENTER_Y_RANGE = (0.20, 0.80)  # on screen to count (filters out
                                       # corner watermark text)

DEBUG = True                       # verbose logging + debug screenshot
```

## Testing the Fallback Flows

Most of the interesting code paths (missing Tesseract, missing game exe,
missing dependencies) don't naturally trigger on a dev machine that
already has everything set up. A few env-var hooks exist specifically so
you can exercise them without touching your real install:

```powershell
# Force the "Tesseract not found" dialog, even though it's installed:
$env:WUWA_TEST_NO_TESSERACT="1"
python wuwa-apr.py

# Force straight past auto-detection to the browse dialog:
$env:WUWA_TEST_NO_CONFIG="1"
$env:WUWA_TEST_NO_AUTODETECT="1"
python wuwa-apr.py
```

Note `WUWA_TEST_NO_CONFIG` is needed alongside `WUWA_TEST_NO_AUTODETECT`, once the game's been found once, its path is cached in `config.json` and
takes priority over everything else, so the autodetect skip alone won't
reach the browse dialog if a path is already remembered.

For testing the pip auto-installer itself, use a throwaway virtual
environment instead (env vars can't fake missing packages, since real
package installation is what's actually being tested):

```powershell
python -m venv test_env
test_env\Scripts\python.exe wuwa-apr.py
```

Delete the `test_env` folder afterward. Your main Python install is never
touched.

## Building the .exe

```powershell
pyinstaller --onefile --windowed --icon=wuwa.ico --uac-admin --add-data "icon.png;." wuwa-apr.py
```

- `--windowed` suppresses the console window (the app is tray/dialog-driven).
- `--icon=wuwa.ico` sets the exe's file/taskbar icon.
- `--uac-admin` embeds a manifest so Windows handles the elevation prompt
  natively, before the exe even starts.
- `--add-data "icon.png;."` bundles the tray icon image so it's available
  at runtime via the frozen exe's temp extraction folder.

The built exe will be in `dist/`.

## Where Data Is Stored

Both the source script and the built exe write their log, debug
screenshot, and remembered game path to:

```
%APPDATA%\WuWaWatchdog\
```

This is deliberate (see `get_app_dir()` in `wuwa-apr.py`), it keeps
persistent data out of the project folder and out of wherever the exe
happens to be run from, so it survives the exe being moved, renamed, or
rebuilt.

## Important Considerations 

- **pywin32 needs a post-install step.** A plain `pip install pywin32`
  can report success while `import win32gui` still fails with
  `ModuleNotFoundError`, pywin32 needs its DLLs copied into place
  separately, which `ensure_dependencies()` now handles automatically by
  running `pywin32_postinstall.py -install` when needed. If you ever hit
  this manually, run:
  ```powershell
  python Scripts\pywin32_postinstall.py -install
  ```
- **`ensure_dependencies()` is a no-op in the frozen exe.** It checks
  `sys.frozen` and returns immediately, since a frozen exe already has
  every dependency bundled and has no real `pip` to call. This bootstrap
  only matters when running the raw `.py`.
- **Building the exe requires a working dev environment.** PyInstaller
  can only bundle modules it can actually import at build time, if
  pywin32 isn't fully working (see above) on the machine doing the build,
  the build itself will fail with the same error, even though end users
  of the resulting exe would never see it.

# WuWa-AutoPatchRestart

Tired of manually restarting Wuthering Waves after patches, or missing the restart prompt because you weren't watching?This script handles the restart automatically and waits until the game is ready to play.

A small Python watchdog that automatically restarts Wuthering Waves when it shows:

> Patching complete. Please restart the game

It keeps restarting the game (will do it once, mostly) until it detects:

> Tap to land in Solaris-3

Then it stops and leaves the game open.

## Table of Contents

- [Requirements](#requirements)
- [Setup](#setup)
  - [1. Clone this repository](#1-clone-this-repository)
  - [2. Install Python](#2-install-python)
  - [3. Install Tesseract OCR](#3-install-tesseract-ocr)
  - [4. Install Python dependencies](#4-install-python-dependencies)
  - [5. Configure](#5-configure)
- [Run](#run)
- [How It Works](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Disclaimer](#disclaimer)
- [AI Assistance](#ai-assistance)
- [License](#license)

## Requirements

- Windows 10/11
- Python 3.13+
- Tesseract OCR
- Wuthering Waves duh

## Setup

### 1. Clone this repository

#### Install Git 

Download Git for Windows:

https://git-scm.com/downloads/win

After installing, verify it with:

```bash
git --version
```

and clone this repo with:
```
git clone https://github.com/KurumiZph/WuWa-AutoPatchReboot.git
cd WuWa-AutoPatchReboot
```

### 2. Install Python

Download Python:
https://www.python.org/downloads/windows/

During installation, enable **Add Python to PATH**.

Check that Python is installed:

```bash
python --version
```

### 3. Install Tesseract OCR

Download the Windows installer:

https://github.com/UB-Mannheim/tesseract/wiki

The default installation path should be:

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

### 4. Install Python dependencies

Open a terminal in the project folder and run:

```bash
python -m pip install -r requirements.txt
```

### 5. Configure

Edit  `wuwa-apr.py` and set:

```python
GAME_EXE = r"C:\Path\To\Wuthering Waves.exe"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

Change `GAME_EXE` to the location of your Wuthering Waves installation.

For Example:

```python
GAME_EXE = r"C:\Steam\steamapps\common\Wuthering Waves\Wuthering Waves.exe"
```

#### Additional Configuration
```python
# How long to wait before restarting Wuthering Waves after
# the "Patching complete. Please restart the game" screen is detected.
RESTART_WAIT_SECONDS = 15

# How often the script checks the game screen for the
# patch or login screen.
CHECK_INTERVAL_SECONDS = 1.0

# Number of consecutive times the login screen must be detected
# before the script considers it confirmed and stops.
LOGIN_CONFIRMATIONS_REQUIRED = 2

# Number of consecutive times the patch screen must be detected
# before the script closes and restarts the game.
PATCH_CONFIRMATIONS_REQUIRED = 2
```

## Run

Use a terminal to run:

```bash
python wuwa-apr.py
```

or double click the script.

### Recommended: Create a shortcut for wuwa-apr.py and use it as your default Wuthering Waves launch shortcut.

## How it works

1. Starts Wuthering Waves.
2. Finds the game window.
3. Uses Tesseract OCR to read the game screen.
4. Detects the patch restart message.
5. Closes and restarts the game.
6. Checks the screen again.
7. Stops when Tap to land in Solaris-3 is detected.
8. Leaves Wuthering Waves open.

The script uses the actual game window instead of fixed screen coordinates, so different resolutions and monitors should work.

## Troubleshooting

If detection isn't working, enable:

```python
DEBUG = True
```

The script can generate:

```text
ocr_log.txt
ocr_debug.png
```

These can help determine what Tesseract is reading.

### Do not upload debug screenshots publicly if they contain private information.

## Limitations

This project relies on OCR and the current Wuthering Waves UI.

Future game updates may change the text or UI.

OCR accuracy can also vary depending on resolution, display scaling, and the game UI.

The script does not work with game minimized.

## Disclaimer

This is an unofficial community project and is not affiliated with Kuro Games or Wuthering Waves.

Use it at your own risk and make sure you're following the game's Terms of Service.

## AI Assistance

This project was developed with assistance from OpenAI's ChatGPT.

ChatGPT helped with the Python implementation and Windows process handling.

The project is maintained and tested by the repository owner.

## License

MIT License

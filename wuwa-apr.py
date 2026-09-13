import os
import re
import ctypes
from ctypes import wintypes
import sys
import time
import subprocess
import difflib
import importlib
import atexit
import winreg
from pathlib import Path

# ---------------- AUTO-INSTALL DEPENDENCIES ----------------
# Checks for required packages and pip-installs anything missing,
# so users don't have to run `pip install -r requirements.txt` by hand.
# NOTE: this only covers Python packages. Tesseract-OCR itself is a
# separate .exe and can't be installed this way -- see TESSERACT_PATH.

REQUIRED_PACKAGES = {
    "PIL": "Pillow>=11.0.0",
    "pytesseract": "pytesseract>=0.3.13",
    "psutil": "psutil>=6.0.0",
    "win32gui": "pywin32>=306",
}


def ensure_dependencies():
    missing = []
    for module_name, pip_spec in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_spec)

    if not missing:
        return

    print(f"[SETUP] Installing missing packages: {', '.join(missing)}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except subprocess.CalledProcessError as e:
        print(f"[SETUP] Failed to install dependencies: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)


ensure_dependencies()

import pytesseract
import psutil
import webbrowser
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageEnhance, ImageFilter

import win32gui
import win32process
import win32con
import win32ui


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        " ".join(f'"{arg}"' for arg in sys.argv), None, 1,
    )
    sys.exit()

# ============================================================
# WUTHERING WAVES OCR WATCHDOG
# Launches WuWa, OCRs the window, detects login/patch screens,
# and auto-restarts the game on patch-complete. No fixed coords.
# ============================================================

# ---------------- CONFIG ----------------

GAME_EXE = r"E:\SteamLibrary\steamapps\common\Wuthering Waves\Wuhering Waves.exe"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_DOWNLOAD_URL = "https://github.com/UB-Mannheim/tesseract/wiki"

RESTART_WAIT_SECONDS = 15          # wait after "please restart" before relaunch
CHECK_INTERVAL_SECONDS = 1.0       # seconds between OCR scans

LOGIN_CONFIRMATIONS_REQUIRED = 2   # consecutive hits needed to confirm
PATCH_CONFIRMATIONS_REQUIRED = 2

LOGIN_MATCH_THRESHOLD = 0.72       # fuzzy-match thresholds (1.0 = exact)
PATCH_MATCH_THRESHOLD = 0.68

# Restart popup must appear within this fraction of screen width/height
# (0.0 = left/top edge, 1.0 = right/bottom edge) to count as real, so
# corner text (like the version watermark) can't trigger a false restart.
PATCH_CENTER_X_RANGE = (0.15, 0.85)
PATCH_CENTER_Y_RANGE = (0.20, 0.80)

MAX_OCR_DIMENSION = 2200           # downscale cap so OCR stays fast at 4K

DEBUG = True                       # verbose logging + debug screenshot
DEBUG_SCREENSHOT_EVERY_N = 3       # save debug PNG every Nth scan (perf)

GAME_PROCESS_NAMES = {
    "Wuthering Waves.exe",
    "Client-Win64-Shipping.exe",
    "WutheringWaves.exe",
}

LOGIN_TARGET = "tap to land in solaris 3"
PATCH_TARGETS = ["patching complete", "please restart the game"]

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_FILE = SCRIPT_DIR / "ocr_log.txt"
DEBUG_SCREENSHOT = SCRIPT_DIR / "ocr_debug.png"

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ---------------- LOGGING ----------------
# Keeps one file handle open for the whole run instead of reopening
# ocr_log.txt on every single log() call (matters a lot in DEBUG mode,
# which logs every OCR word on every scan).

_log_handle = None


def _get_log_handle():
    global _log_handle
    if _log_handle is None:
        try:
            _log_handle = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        except Exception:
            _log_handle = False  # sentinel: open failed, don't retry every call
    return _log_handle


def _close_log_handle():
    global _log_handle
    if _log_handle:
        try:
            _log_handle.close()
        except Exception:
            pass
        _log_handle = None


atexit.register(_close_log_handle)


def log(message):
    print(message)
    handle = _get_log_handle()
    if handle:
        try:
            handle.write(message + "\n")
        except Exception:
            pass


# ---------------- TEXT NORMALIZATION ----------------

def normalize_text(text):
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().replace("-", " ").replace("_", " ").replace("–", " ").replace("—", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------- GAME PROCESS DETECTION ----------------

def get_game_processes():
    processes = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if process.info["name"] in GAME_PROCESS_NAMES:
                processes.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return processes


def is_game_running():
    return len(get_game_processes()) > 0


# ---------------- FIND GAME WINDOWS ----------------

def find_game_windows():
    """Find visible top-level windows belonging to a WuWa process."""
    game_pids = {p.pid for p in get_game_processes()}
    windows = []

    def enum_callback(hwnd, extra):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width < 500 or height < 300:
                return True
        except Exception:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in game_pids:
                return True
        except Exception:
            return True

        windows.append({
            "hwnd": hwnd, "pid": pid, "title": win32gui.GetWindowText(hwnd),
            "rect": (left, top, right, bottom), "width": width, "height": height,
        })
        return True

    try:
        win32gui.EnumWindows(enum_callback, None)
    except Exception as e:
        log(f"[WINDOW] EnumWindows error: {e}")

    return windows


def find_best_game_window():
    windows = find_game_windows()
    if not windows:
        return None
    windows.sort(key=lambda w: w["width"] * w["height"], reverse=True)
    return windows[0]


def wait_for_game_window(timeout=60):
    log("[WINDOW] Waiting for Wuthering Waves window...")
    start = time.time()
    while True:
        window = find_best_game_window()
        if window:
            log("[WINDOW] Found WuWa window:")
            log(f"          Title: {window['title']}")
            log(f"          Size: {window['width']}x{window['height']}")
            return window
        if timeout is not None and time.time() - start > timeout:
            return None
        time.sleep(1)


# ---------------- WINDOW CAPTURE ----------------

def capture_game_window(window):
    """
    Capture only the WuWa window via PrintWindow (PW_RENDERFULLCONTENT),
    so GPU-rendered frames come through instead of a black bitmap.
    """
    hwnd = window["hwnd"]
    if not win32gui.IsWindow(hwnd):
        return None

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None

    hwnd_dc = src_dc = mem_dc = bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None

        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()

        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        mem_dc.SelectObject(bitmap)

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        print_window = user32.PrintWindow
        print_window.restype = wintypes.BOOL
        print_window.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]

        result = print_window(
            wintypes.HWND(int(hwnd)),
            wintypes.HDC(int(mem_dc.GetSafeHdc())),
            2,  # PW_RENDERFULLCONTENT
        )

        if not result:
            log(f"[CAPTURE] PrintWindow failed (error={ctypes.get_last_error()}).")
            return None

        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
        return image.copy()

    except Exception as e:
        log(f"[CAPTURE] Window capture error: {e}")
        return None

    finally:
        try:
            if mem_dc is not None:
                mem_dc.DeleteDC()
        except Exception:
            pass
        try:
            if src_dc is not None:
                src_dc.DeleteDC()
        except Exception:
            pass
        try:
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetSafeHandle())
        except Exception:
            pass


def prepare_for_ocr(image):
    """Grayscale + downscale + contrast/sharpen for Tesseract."""
    image = image.convert("L")
    width, height = image.size
    largest = max(width, height)

    if largest > MAX_OCR_DIMENSION:
        scale = MAX_OCR_DIMENSION / largest
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        # BILINEAR: faster than LANCZOS, plenty sharp enough for OCR
        image = image.resize(new_size, Image.Resampling.BILINEAR)

    image = ImageEnhance.Contrast(image).enhance(2.2)
    image = image.filter(ImageFilter.SHARPEN)
    return image


# ---------------- OCR ----------------

def perform_ocr(image):
    processed = prepare_for_ocr(image)

    try:
        data = pytesseract.image_to_data(
            processed, lang="eng", config="--oem 1 --psm 11",
            output_type=pytesseract.Output.DICT,
        )
    except Exception as e:
        log(f"[OCR ERROR] {e}")
        return {"text": "", "words": [], "image": processed}

    words = []
    for i in range(len(data["text"])):
        raw_text = data["text"][i].strip()
        if not raw_text:
            continue
        try:
            confidence = float(data["conf"][i])
        except Exception:
            confidence = 0
        if confidence < 10:  # drop low-confidence garbage
            continue
        word = normalize_text(raw_text)
        if not word:
            continue
        words.append({
            "text": word, "confidence": confidence,
            "left": data["left"][i], "top": data["top"][i],
            "width": data["width"][i], "height": data["height"][i],
        })

    combined_text = " ".join(w["text"] for w in words)
    return {"text": combined_text, "words": words, "image": processed}


# ---------------- FUZZY TEXT MATCHING ----------------

def similarity(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_fuzzy_phrase(words, target, threshold):
    """Slide a window over OCR words looking for a fuzzy match to target."""
    target = normalize_text(target)
    target_words = target.split()
    if not target_words:
        return None

    target_count = len(target_words)
    for window_size in range(max(1, target_count - 1), target_count + 3):
        for start in range(0, len(words) - window_size + 1):
            window = words[start:start + window_size]
            candidate = " ".join(w["text"] for w in window)
            score = similarity(target, candidate)
            if score >= threshold:
                return {"score": score, "candidate": candidate, "words": window}

    return None


# ---------------- DETECTION ----------------

def detect_login(ocr):
    result = find_fuzzy_phrase(ocr["words"], LOGIN_TARGET, LOGIN_MATCH_THRESHOLD)
    if not result:
        return None

    # Position isn't required, just a confidence boost (login text sits low).
    matched_words = result["words"]
    avg_top = sum(w["top"] for w in matched_words) / len(matched_words)
    relative_y = avg_top / max(1, ocr["image"].height)
    bottom_position = relative_y >= 0.65

    score = min(result["score"] + (0.05 if bottom_position else 0), 1.0)
    return {"score": score, "candidate": result["candidate"], "bottom_position": bottom_position}


def detect_patch(ocr):
    """
    Require BOTH "patching complete" and "please restart the game" to be
    present (not just one), AND require them to sit roughly centered on
    screen -- like the actual restart popup does. This avoids false
    positives from things like the permanent bottom-corner version text,
    which only ever shows "patching complete" on its own.
    """
    words = ocr["words"]

    matches = []
    for target in PATCH_TARGETS:
        result = find_fuzzy_phrase(words, target, PATCH_MATCH_THRESHOLD)
        if not result:
            return None  # all targets must be found, not just one
        matches.append(result)

    matched_words = [w for r in matches for w in r["words"]]
    if not matched_words:
        return None

    width, height = ocr["image"].width, ocr["image"].height
    avg_left = sum(w["left"] for w in matched_words) / len(matched_words)
    avg_top = sum(w["top"] for w in matched_words) / len(matched_words)
    rel_x = avg_left / max(1, width)
    rel_y = avg_top / max(1, height)

    x_lo, x_hi = PATCH_CENTER_X_RANGE
    y_lo, y_hi = PATCH_CENTER_Y_RANGE
    if not (x_lo <= rel_x <= x_hi and y_lo <= rel_y <= y_hi):
        return None  # text found, but not where the popup actually appears

    score = min(r["score"] for r in matches)
    candidate = " | ".join(r["candidate"] for r in matches)
    return {"score": score, "candidate": candidate}


def save_debug_screenshot(image):
    if not DEBUG:
        return
    try:
        image.save(DEBUG_SCREENSHOT)
    except Exception as e:
        log(f"[DEBUG] Could not save screenshot: {e}")


# ---------------- CLOSE / LAUNCH GAME ----------------

def close_game():
    processes = get_game_processes()
    if not processes:
        log("[WuWa] No game process found.")
        return

    log("[WuWa] Closing Wuthering Waves...")
    for process in processes:
        try:
            log(f"[WuWa] Terminating {process.info['name']} (PID {process.pid})")
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    deadline = time.time() + 6
    while time.time() < deadline:
        if not is_game_running():
            log("[WuWa] Game closed.")
            return
        time.sleep(0.25)

    log("[WuWa] Game did not close normally. Force closing...")
    for process in get_game_processes():
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    deadline = time.time() + 5
    while time.time() < deadline:
        if not is_game_running():
            log("[WuWa] Game force-closed.")
            return
        time.sleep(0.25)

    log("[WuWa] WARNING: WuWa process may still be running.")


# ---------------- AUTO-FIND GAME EXE (STEAM) ----------------
# Fallback for when the hardcoded GAME_EXE path is wrong, missing, or the
# game got moved/reinstalled to a different Steam library.

def find_steam_install_path():
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, subkey, value_name in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                if value and os.path.isdir(value):
                    return value
        except OSError:
            continue
    return None


def find_steam_library_folders():
    steam_path = find_steam_install_path()
    if not steam_path:
        return []

    libraries = [steam_path]
    vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")

    try:
        with open(vdf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for match in re.finditer(r'"path"\s*"([^"]+)"', content):
            path = match.group(1).replace("\\\\", "\\")
            if path not in libraries:
                libraries.append(path)
    except Exception:
        pass

    return libraries


def find_game_exe_auto():
    """Scan every Steam library for the WuWa install folder."""
    exe_names = ["Wuthering Waves.exe", "Client-Win64-Shipping.exe"]

    for library in find_steam_library_folders():
        base = os.path.join(library, "steamapps", "common", "Wuthering Waves")
        for name in exe_names:
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate):
                return candidate

    return None


def launch_game():
    if not os.path.isfile(GAME_EXE):
        log(f"\n[ERROR] Game executable was not found:\n        {GAME_EXE}")
        log("\nEdit GAME_EXE at the top of WuWa.py.")
        return False

    log("[WuWa] Launching game...")
    try:
        subprocess.Popen(
            [GAME_EXE], cwd=os.path.dirname(GAME_EXE),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except Exception as e:
        log(f"[WuWa] Launch error: {e}")
        return False


def wait_for_game_process():
    log("[WuWa] Waiting for game process...")
    while not is_game_running():
        time.sleep(1)
    log("[WuWa] Game process detected.")


def wait_for_window_after_launch():
    wait_for_game_process()
    return wait_for_game_window(timeout=120)


def restart_countdown():
    log(f"\n[WuWa] Waiting {RESTART_WAIT_SECONDS} seconds before restarting...")
    for remaining in range(RESTART_WAIT_SECONDS, 0, -1):
        print(f"\r[WuWa] Restarting in {remaining:3d} seconds...", end="", flush=True)
        time.sleep(1)
    print()


# ---------------- MAIN OCR LOOP ----------------

def monitor_game():
    login_count = 0
    patch_count = 0
    scan_number = 0

    while True:
        # Re-find the window each loop since restarts change the HWND.
        window = find_best_game_window()
        if not window:
            if not is_game_running():
                log("[WuWa] Game process disappeared. Waiting for it to return...")
                wait_for_game_process()
            window = wait_for_game_window(timeout=30)
            if not window:
                time.sleep(1)
                continue

        image = capture_game_window(window)
        if image is None:
            time.sleep(1)
            continue

        scan_number += 1
        if DEBUG and scan_number % DEBUG_SCREENSHOT_EVERY_N == 0:
            save_debug_screenshot(image)

        ocr = perform_ocr(image)

        if DEBUG:
            log("\n[DEBUG] OCR WORDS:")
            for word in ocr["words"]:
                log(f"    '{word['text']}' conf={word['confidence']:.1f} "
                    f"x={word['left']} y={word['top']} w={word['width']} h={word['height']}")
            log("")

        login = detect_login(ocr)
        patch = detect_patch(ocr)

        if login:
            login_count += 1
            log(f"[LOGIN] Possible detection {login_count}/{LOGIN_CONFIRMATIONS_REQUIRED} "
                f"| score={login['score']:.2f} | text='{login['candidate']}'")
        else:
            login_count = 0

        if patch:
            patch_count += 1
            log(f"[PATCH] Possible detection {patch_count}/{PATCH_CONFIRMATIONS_REQUIRED} "
                f"| score={patch['score']:.2f} | text='{patch['candidate']}'")
        else:
            patch_count = 0

        if login_count >= LOGIN_CONFIRMATIONS_REQUIRED:
            log("\n" + "=" * 60)
            log("[SUCCESS] LOGIN SCREEN CONFIRMED")
            log("=" * 60)
            log(f"\nDetected:\n    {login['candidate']}\n")
            log("Wuthering Waves will remain running.")
            log("The watchdog is exiting.\n")
            return

        if patch_count >= PATCH_CONFIRMATIONS_REQUIRED:
            log("\n" + "=" * 60)
            log("[PATCH] RESTART MESSAGE CONFIRMED")
            log("=" * 60)
            log(f"\nDetected: {patch['candidate']}\n")

            login_count = 0
            patch_count = 0

            close_game()
            restart_countdown()

            if launch_game():
                log("[WuWa] Restart launched.")
                new_window = wait_for_window_after_launch()
                log("[WuWa] New game window detected." if new_window
                    else "[WuWa] WARNING: Game window was not found yet.")
            else:
                log("[WuWa] Restart failed.")
                return

            continue

        time.sleep(CHECK_INTERVAL_SECONDS)


# ---------------- MAIN ----------------

# ---------------- TESSERACT CHECK ----------------

def ensure_tesseract_installed():
    """
    If Tesseract isn't found, pop up a small dialog offering to open the
    download page. Tesseract is a real .exe, not a pip package, so this
    can't auto-install it -- just point the user at the installer.
    """
    if os.path.isfile(TESSERACT_PATH):
        return True

    root = tk.Tk()
    root.withdraw()

    while not os.path.isfile(TESSERACT_PATH):
        wants_download = messagebox.askyesno(
            "Tesseract-OCR Not Found",
            "This script needs Tesseract-OCR, which isn't installed "
            f"at:\n\n{TESSERACT_PATH}\n\n"
            "Open the download page now?",
        )

        if not wants_download:
            root.destroy()
            return False

        webbrowser.open(TESSERACT_DOWNLOAD_URL)

        keep_waiting = messagebox.askokcancel(
            "Waiting for Install",
            "Install Tesseract-OCR (default install path recommended), "
            "then click OK to continue.\n\n"
            "Click Cancel to give up and exit.",
        )

        if not keep_waiting:
            root.destroy()
            return False

    root.destroy()
    return True


def main():
    print("\n" + "=" * 60)
    print("       WUTHERING WAVES OCR WATCHDOG")
    print("=" * 60 + "\n")

    # Must run before any log() call: log() now keeps the file open for
    # the whole session, and Windows can't delete a file that's open.
    try:
        LOG_FILE.unlink()
    except FileNotFoundError:
        pass

    if not ensure_tesseract_installed():
        print("[ERROR] Tesseract-OCR is required. Exiting.")
        input("\nPress Enter to exit...")
        return
    log("[SYSTEM] Tesseract found.")

    global GAME_EXE
    if not os.path.isfile(GAME_EXE):
        auto_path = find_game_exe_auto()
        if auto_path:
            log(f"[SYSTEM] Configured GAME_EXE not found, auto-detected via Steam: {auto_path}")
            GAME_EXE = auto_path
        else:
            print(f"\n[ERROR] Wuthering Waves executable not found:\n{GAME_EXE}")
            print("\nCould not auto-detect it via Steam either.")
            print("Edit GAME_EXE at the top of WuWa.py.")
            input("\nPress Enter to exit...")
            return
    log("[SYSTEM] Game executable found.")

    if is_game_running():
        log("[WuWa] Game is already running.")
    elif not launch_game():
        input("\nPress Enter to exit...")
        return

    window = wait_for_window_after_launch()
    if not window:
        log("\n[ERROR] Could not find WuWa window.")
        log("The game may still be starting.")
        input("\nPress Enter to exit...")
        return

    log("\n" + "=" * 60)
    log("[OCR] MONITORING STARTED")
    log("=" * 60)
    log("\n[OCR] Looking for:")
    log(f"       LOGIN  = {LOGIN_TARGET}")
    log("       PATCH  = Patching complete / Please restart")
    log("\n[OCR] Entire WuWa window is scanned.")
    log("[OCR] Screen resolution does not need to be configured.")
    log("\n[OCR] Press Ctrl+C to stop.\n")

    try:
        monitor_game()
    except KeyboardInterrupt:
        print("\n")
        log("[SYSTEM] Watchdog stopped by user.")
    except Exception as e:
        log(f"\n[FATAL ERROR] {e}")
        log("The game has NOT been intentionally closed.")


if __name__ == "__main__":
    main()
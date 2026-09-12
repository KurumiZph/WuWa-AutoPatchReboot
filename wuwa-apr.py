import os
import re
import ctypes
import sys
import time
import subprocess
import difflib
from pathlib import Path

import mss
import pytesseract
import psutil

from PIL import Image, ImageEnhance, ImageFilter

import win32gui
import win32process
import win32con

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        " ".join(f'"{arg}"' for arg in sys.argv),
        None,
        1,
    )
    sys.exit()
    
# ============================================================
# WUTHERING WAVES OCR WATCHDOG
# ============================================================
#
# What this program does:
#
#   1. Starts Wuthering Waves.
#   2. Finds the actual WuWa window.
#   3. Captures the ENTIRE game window.
#   4. OCRs the whole window.
#   5. Looks for:
#
#       LOGIN:
#       "Tap to land in Solaris-3"
#
#       PATCH RESTART:
#       "Patching complete"
#       "Please restart the game"
#
#   6. Requires multiple consecutive detections to avoid
#      false positives.
#
#   7. LOGIN detected:
#          Exit this script.
#          Leave WuWa running.
#
#   8. PATCH detected:
#          Close WuWa.
#          Wait.
#          Start WuWa again.
#
#
# The script does NOT use fixed screen coordinates for detection.
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

# ------------------------------------------------------------
# WUTHERING WAVES EXECUTABLE
# ------------------------------------------------------------
#
# CHANGE THIS TO YOUR ACTUAL GAME EXE.
#
# Example:
#
# GAME_EXE = r"E:\SteamLibrary\steamapps\common\Wuthering Waves\Wuthering Waves.exe"
#
# ------------------------------------------------------------

GAME_EXE = r"E:\SteamLibrary\steamapps\common\Wuthering Waves\Wuthering Waves.exe"


# ------------------------------------------------------------
# TESSERACT
# ------------------------------------------------------------
#
# Change this if Tesseract is installed elsewhere.
# ------------------------------------------------------------

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# ------------------------------------------------------------
# RESTART DELAY
# ------------------------------------------------------------
#
# How many seconds to wait after:
#
# "Patching complete. Please restart the game."
#
# ------------------------------------------------------------

RESTART_WAIT_SECONDS = 15


# ------------------------------------------------------------
# OCR INTERVAL
# ------------------------------------------------------------

CHECK_INTERVAL_SECONDS = 1.0


# ------------------------------------------------------------
# DETECTION CONFIRMATION
# ------------------------------------------------------------
#
# The text must be detected this many consecutive times before
# the watchdog acts.
#
# This protects against one bad OCR frame.
# ------------------------------------------------------------

LOGIN_CONFIRMATIONS_REQUIRED = 2
PATCH_CONFIRMATIONS_REQUIRED = 2


# ------------------------------------------------------------
# FUZZY MATCH THRESHOLD
# ------------------------------------------------------------
#
# 1.00 = exact match
#
# Lower values tolerate more OCR mistakes.
#
# 0.70 - 0.80 is generally useful for OCR.
# ------------------------------------------------------------

LOGIN_MATCH_THRESHOLD = 0.72

PATCH_MATCH_THRESHOLD = 0.68


# ------------------------------------------------------------
# OCR IMAGE SIZE
# ------------------------------------------------------------
#
# At 4K, OCRing the entire image can be unnecessarily slow.
#
# The screenshot is automatically resized so its longest side
# is no larger than this value.
#
# This DOES NOT affect detection coordinates because OCR
# coordinates are only used internally.
# ------------------------------------------------------------

MAX_OCR_DIMENSION = 2200


# ------------------------------------------------------------
# DEBUG
# ------------------------------------------------------------
#
# True:
#   Prints OCR information to ocr_log.txt.
#
# False:
#   Less logging.
#
# Keep True initially so we can diagnose anything unusual.
# ------------------------------------------------------------

DEBUG = True


# ------------------------------------------------------------
# GAME PROCESS NAMES
# ------------------------------------------------------------
#
# WuWa may use different process names depending on version.
#
# Add another one here if necessary.
# ------------------------------------------------------------

GAME_PROCESS_NAMES = {
    "Wuthering Waves.exe",
    "Client-Win64-Shipping.exe",
    "WutheringWaves.exe",
}


# ============================================================
# EXPECTED TEXT
# ============================================================

LOGIN_TARGET = "tap to land in solaris 3"

PATCH_TARGETS = [
    "patching complete",
    "please restart the game",
]


# ============================================================
# PATHS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

LOG_FILE = SCRIPT_DIR / "ocr_log.txt"

DEBUG_SCREENSHOT = SCRIPT_DIR / "ocr_debug.png"


# ============================================================
# TESSERACT SETUP
# ============================================================

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ============================================================
# LOGGING
# ============================================================

def log(message):

    print(message)

    try:

        with open(
            LOG_FILE,
            "a",
            encoding="utf-8"
        ) as file:

            file.write(message + "\n")

    except Exception:
        pass


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text):
    """
    Normalize OCR text.

    Examples:

        "Tap to land in Solaris-3"
            ->
        "tap to land in solaris 3"

        "Patching complete."
            ->
        "patching complete"
    """

    text = text.lower()

    # Normalize common OCR punctuation.
    text = text.replace("-", " ")
    text = text.replace("_", " ")
    text = text.replace("–", " ")
    text = text.replace("—", " ")

    # Keep letters/numbers/spaces only.
    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text
    )

    # Collapse whitespace.
    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# GAME PROCESS DETECTION
# ============================================================

def get_game_processes():

    processes = []

    for process in psutil.process_iter(
        ["pid", "name"]
    ):

        try:

            name = process.info["name"]

            if name in GAME_PROCESS_NAMES:

                processes.append(process)

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied
        ):
            pass

    return processes


def is_game_running():

    return len(
        get_game_processes()
    ) > 0


# ============================================================
# FIND WINDOWS BELONGING TO WUWA
# ============================================================

def find_game_windows():
    """
    Find visible top-level Windows windows belonging to
    a Wuthering Waves process.

    No resolution assumptions are made here.
    """

    game_pids = {
        process.pid
        for process in get_game_processes()
    }

    windows = []

    def enum_callback(hwnd, extra):

        if not win32gui.IsWindowVisible(hwnd):
            return True

        # Ignore tiny/invisible windows.
        try:

            left, top, right, bottom = (
                win32gui.GetWindowRect(hwnd)
            )

            width = right - left
            height = bottom - top

            if width < 500 or height < 300:
                return True

        except Exception:

            return True

        try:

            _, pid = win32process.GetWindowThreadProcessId(
                hwnd
            )

            if pid not in game_pids:
                return True

        except Exception:

            return True

        title = win32gui.GetWindowText(hwnd)

        windows.append({
            "hwnd": hwnd,
            "pid": pid,
            "title": title,
            "rect": (
                left,
                top,
                right,
                bottom
            ),
            "width": width,
            "height": height,
        })

        return True

    try:

        win32gui.EnumWindows(
            enum_callback,
            None
        )

    except Exception as e:

        log(
            f"[WINDOW] EnumWindows error: {e}"
        )

    return windows


def find_best_game_window():

    windows = find_game_windows()

    if not windows:
        return None

    # Prefer the largest WuWa window.
    windows.sort(
        key=lambda w: (
            w["width"] * w["height"]
        ),
        reverse=True
    )

    return windows[0]


# ============================================================
# WAIT FOR GAME WINDOW
# ============================================================

def wait_for_game_window(timeout=60):

    log(
        "[WINDOW] Waiting for Wuthering Waves window..."
    )

    start = time.time()

    while True:

        window = find_best_game_window()

        if window:

            log(
                "[WINDOW] Found WuWa window:"
            )

            log(
                f"          Title: {window['title']}"
            )

            log(
                f"          Size: "
                f"{window['width']}x{window['height']}"
            )

            return window

        if (
            timeout is not None
            and
            time.time() - start > timeout
        ):

            return None

        time.sleep(1)


# ============================================================
# SCREEN CAPTURE
# ============================================================

def capture_game_window(
    sct,
    window
):
    """
    Capture the actual WuWa window rectangle.

    This means another application window elsewhere on the
    desktop doesn't matter.

    The watchdog console can therefore be minimized or moved
    around without changing OCR coordinates.
    """

    left, top, right, bottom = window["rect"]

    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        return None

    screenshot = sct.grab({
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    })

    image = Image.frombytes(
        "RGB",
        screenshot.size,
        screenshot.rgb
    )

    return image


# ============================================================
# OCR PREPROCESSING
# ============================================================

def prepare_for_ocr(image):
    """
    Prepare screenshot for Tesseract.

    Automatically scales based on image size.

    This is resolution-independent.
    """

    image = image.convert("L")

    width, height = image.size

    largest_dimension = max(
        width,
        height
    )

    if largest_dimension > MAX_OCR_DIMENSION:

        scale = (
            MAX_OCR_DIMENSION /
            largest_dimension
        )

        new_size = (
            max(1, int(width * scale)),
            max(1, int(height * scale))
        )

        image = image.resize(
            new_size,
            Image.Resampling.LANCZOS
        )

    # Increase contrast.
    image = ImageEnhance.Contrast(
        image
    ).enhance(2.2)

    # Sharpen text.
    image = image.filter(
        ImageFilter.SHARPEN
    )

    return image


# ============================================================
# OCR
# ============================================================

def perform_ocr(image):

    processed = prepare_for_ocr(
        image
    )

    try:

        data = pytesseract.image_to_data(
            processed,
            lang="eng",
            config="--oem 1 --psm 11",
            output_type=pytesseract.Output.DICT
        )

    except Exception as e:

        log(
            f"[OCR ERROR] {e}"
        )

        return {
            "text": "",
            "words": [],
            "image": processed,
        }

    words = []

    total_entries = len(
        data["text"]
    )

    for i in range(total_entries):

        raw_text = data["text"][i].strip()

        if not raw_text:
            continue

        try:

            confidence = float(
                data["conf"][i]
            )

        except Exception:

            confidence = 0

        # Ignore extremely low confidence garbage.
        if confidence < 10:
            continue

        word = normalize_text(
            raw_text
        )

        if not word:
            continue

        words.append({
            "text": word,
            "confidence": confidence,
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data["width"][i],
            "height": data["height"][i],
        })

    combined_text = " ".join(
        word["text"]
        for word in words
    )

    return {
        "text": combined_text,
        "words": words,
        "image": processed,
    }


# ============================================================
# FUZZY TEXT MATCHING
# ============================================================

def similarity(a, b):

    return difflib.SequenceMatcher(
        None,
        a,
        b
    ).ratio()


def find_fuzzy_phrase(
    words,
    target,
    threshold
):
    """
    Search OCR words for a phrase.

    It doesn't require an exact match.

    Example:

        Expected:
            tap to land in solaris 3

        OCR:
            tap to land in solaris ?

    can still be accepted.
    """

    target = normalize_text(
        target
    )

    target_words = target.split()

    if not target_words:
        return None

    target_count = len(
        target_words
    )

    # Search windows slightly larger/smaller than target.
    for window_size in range(
        max(1, target_count - 1),
        target_count + 3
    ):

        for start in range(
            0,
            len(words) - window_size + 1
        ):

            window = words[
                start:
                start + window_size
            ]

            candidate = " ".join(
                word["text"]
                for word in window
            )

            score = similarity(
                target,
                candidate
            )

            if score >= threshold:

                return {
                    "score": score,
                    "candidate": candidate,
                    "words": window,
                }

    return None


# ============================================================
# LOGIN DETECTION
# ============================================================

def detect_login(ocr):

    words = ocr["words"]

    result = find_fuzzy_phrase(
        words,
        LOGIN_TARGET,
        LOGIN_MATCH_THRESHOLD
    )

    if not result:
        return None

    # --------------------------------------------------------
    # Position is NOT required for detection.
    #
    # We only use it as a confidence boost / sanity check.
    #
    # This means resolution changes do not break detection.
    # --------------------------------------------------------

    matched_words = result["words"]

    avg_top = sum(
        word["top"]
        for word in matched_words
    ) / len(matched_words)

    image_height = ocr["image"].height

    relative_y = (
        avg_top /
        max(1, image_height)
    )

    # Login text normally appears toward the bottom.
    # Give extra confidence if that's where it is.
    bottom_position = (
        relative_y >= 0.65
    )

    confidence_score = result["score"]

    if bottom_position:

        confidence_score += 0.05

    return {
        "score": min(
            confidence_score,
            1.0
        ),
        "candidate": result["candidate"],
        "bottom_position": bottom_position,
    }


# ============================================================
# PATCH DETECTION
# ============================================================

def detect_patch(ocr):

    words = ocr["words"]

    results = []

    # --------------------------------------------------------
    # Look for:
    #
    #   Patching complete
    #
    # and:
    #
    #   Please restart the game
    #
    # --------------------------------------------------------

    for target in PATCH_TARGETS:

        result = find_fuzzy_phrase(
            words,
            target,
            PATCH_MATCH_THRESHOLD
        )

        if result:

            results.append({
                "target": target,
                "score": result["score"],
                "candidate": result["candidate"],
                "words": result["words"],
            })

    # --------------------------------------------------------
    # Strong detection:
    #
    # If OCR sees "patching", "complete" and "restart"
    # somewhere in the game window, that's enough to strongly
    # suggest the restart popup.
    # --------------------------------------------------------

    all_text = ocr["text"]

    has_patching = (
        "patching" in all_text
    )

    has_complete = (
        "complete" in all_text
    )

    has_restart = (
        "restart" in all_text
    )

    keyword_detection = (
        has_patching
        and
        has_complete
        and
        has_restart
    )

    if keyword_detection:

        results.append({
            "target": "patching + complete + restart",
            "score": 0.90,
            "candidate": all_text,
            "words": [],
        })

    if not results:
        return None

    results.sort(
        key=lambda result: result["score"],
        reverse=True
    )

    return results[0]


# ============================================================
# DEBUG SCREENSHOT
# ============================================================

def save_debug_screenshot(image):

    if not DEBUG:
        return

    try:

        image.save(
            DEBUG_SCREENSHOT
        )

    except Exception as e:

        log(
            f"[DEBUG] Could not save screenshot: {e}"
        )


# ============================================================
# CLOSE GAME
# ============================================================

def close_game():

    processes = get_game_processes()

    if not processes:

        log(
            "[WuWa] No game process found."
        )

        return

    log(
        "[WuWa] Closing Wuthering Waves..."
    )

    # First attempt graceful termination.
    for process in processes:

        try:

            log(
                f"[WuWa] Terminating "
                f"{process.info['name']} "
                f"(PID {process.pid})"
            )

            process.terminate()

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied
        ):
            pass

    # Wait for graceful termination.
    deadline = time.time() + 6

    while time.time() < deadline:

        if not is_game_running():

            log(
                "[WuWa] Game closed."
            )

            return

        time.sleep(0.25)

    # --------------------------------------------------------
    # Still alive -> force kill.
    # --------------------------------------------------------

    log(
        "[WuWa] Game did not close normally."
    )

    log(
        "[WuWa] Force closing..."
    )

    for process in get_game_processes():

        try:

            process.kill()

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied
        ):
            pass

    # Make sure processes disappear.
    deadline = time.time() + 5

    while time.time() < deadline:

        if not is_game_running():

            log(
                "[WuWa] Game force-closed."
            )

            return

        time.sleep(0.25)

    log(
        "[WuWa] WARNING: WuWa process may still be running."
    )


# ============================================================
# LAUNCH GAME
# ============================================================

def launch_game():

    if not os.path.isfile(
        GAME_EXE
    ):

        log("")
        log(
            "[ERROR] Game executable was not found:"
        )

        log(
            f"        {GAME_EXE}"
        )

        log("")
        log(
            "Edit GAME_EXE at the top of WuWa.py."
        )

        return False

    log(
        "[WuWa] Launching game..."
    )

    try:

        subprocess.Popen(
            [GAME_EXE],
            cwd=os.path.dirname(
                GAME_EXE
            ),
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
            )
        )

        return True

    except Exception as e:

        log(
            f"[WuWa] Launch error: {e}"
        )

        return False


# ============================================================
# WAIT FOR PROCESS
# ============================================================

def wait_for_game_process():

    log(
        "[WuWa] Waiting for game process..."
    )

    while not is_game_running():

        time.sleep(1)

    log(
        "[WuWa] Game process detected."
    )


# ============================================================
# WAIT FOR WINDOW
# ============================================================

def wait_for_window_after_launch():

    # First wait for process.
    wait_for_game_process()

    # Then wait for actual graphical window.
    window = wait_for_game_window(
        timeout=120
    )

    return window


# ============================================================
# RESTART COUNTDOWN
# ============================================================

def restart_countdown():

    log("")
    log(
        f"[WuWa] Waiting "
        f"{RESTART_WAIT_SECONDS} seconds "
        f"before restarting..."
    )

    for remaining in range(
        RESTART_WAIT_SECONDS,
        0,
        -1
    ):

        print(
            f"\r[WuWa] Restarting in "
            f"{remaining:3d} seconds...",
            end="",
            flush=True
        )

        time.sleep(1)

    print()


# ============================================================
# MAIN OCR LOOP
# ============================================================

def monitor_game():

    login_count = 0
    patch_count = 0

    last_logged_ocr = ""

    with mss.mss() as sct:

        while True:

            # ------------------------------------------------
            # Find current WuWa window.
            #
            # We do this repeatedly because after a restart
            # the window handle can change.
            # ------------------------------------------------

            window = find_best_game_window()

            if not window:

                if not is_game_running():

                    log(
                        "[WuWa] Game process disappeared."
                    )

                    log(
                        "[WuWa] Waiting for it to return..."
                    )

                    wait_for_game_process()

                window = wait_for_game_window(
                    timeout=30
                )

                if not window:

                    time.sleep(1)
                    continue

            # ------------------------------------------------
            # Capture entire game window.
            # ------------------------------------------------

            image = capture_game_window(
                sct,
                window
            )

            if image is None:

                time.sleep(1)
                continue

            # ------------------------------------------------
            # OCR entire game window.
            # ------------------------------------------------

            ocr = perform_ocr(
                image
            )

            text = ocr["text"]

            # ------------------------------------------------
            # DEBUG
            # ------------------------------------------------

            if DEBUG:

                # Don't spam identical OCR results.
                if text != last_logged_ocr:

                    log(
                        f"[OCR] {text}"
                    )

                    last_logged_ocr = text

            # ------------------------------------------------
            # Detect login.
            # ------------------------------------------------

            login = detect_login(
                ocr
            )

            # ------------------------------------------------
            # Detect patch restart.
            # ------------------------------------------------

            patch = detect_patch(
                ocr
            )

            # =================================================
            # LOGIN
            # =================================================

            if login:

                login_count += 1

                log(
                    f"[LOGIN] Possible detection "
                    f"{login_count}/"
                    f"{LOGIN_CONFIRMATIONS_REQUIRED} "
                    f"| score={login['score']:.2f} "
                    f"| text='{login['candidate']}'"
                )

            else:

                login_count = 0

            # =================================================
            # PATCH
            # =================================================

            if patch:

                patch_count += 1

                log(
                    f"[PATCH] Possible detection "
                    f"{patch_count}/"
                    f"{PATCH_CONFIRMATIONS_REQUIRED} "
                    f"| score={patch['score']:.2f} "
                    f"| text='{patch['candidate']}'"
                )

            else:

                patch_count = 0

            # =================================================
            # LOGIN CONFIRMED
            # =================================================

            if (
                login_count
                >=
                LOGIN_CONFIRMATIONS_REQUIRED
            ):

                log("")
                log("=" * 60)
                log(
                    "[SUCCESS] LOGIN SCREEN CONFIRMED"
                )
                log("=" * 60)
                log("")
                log(
                    "Detected:"
                )
                log(
                    f"    {login['candidate']}"
                )
                log("")
                log(
                    "Wuthering Waves will remain running."
                )
                log(
                    "The watchdog is exiting."
                )
                log("")

                return

            # =================================================
            # PATCH CONFIRMED
            # =================================================

            if (
                patch_count
                >=
                PATCH_CONFIRMATIONS_REQUIRED
            ):

                log("")
                log("=" * 60)
                log(
                    "[PATCH] RESTART MESSAGE CONFIRMED"
                )
                log("=" * 60)
                log("")
                log(
                    f"Detected: {patch['candidate']}"
                )
                log("")

                # Reset counters.
                login_count = 0
                patch_count = 0

                # Close WuWa.
                close_game()

                # Wait.
                restart_countdown()

                # Launch again.
                if launch_game():

                    log(
                        "[WuWa] Restart launched."
                    )

                    # Wait until new game window appears.
                    new_window = (
                        wait_for_window_after_launch()
                    )

                    if new_window:

                        log(
                            "[WuWa] New game window detected."
                        )

                    else:

                        log(
                            "[WuWa] WARNING: "
                            "Game window was not found yet."
                        )

                else:

                    log(
                        "[WuWa] Restart failed."
                    )

                    return

                continue

            # ------------------------------------------------
            # Wait before next OCR scan.
            # ------------------------------------------------

            time.sleep(
                CHECK_INTERVAL_SECONDS
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("       WUTHERING WAVES OCR WATCHDOG")
    print("=" * 60)
    print()

    # --------------------------------------------------------
    # Check Tesseract.
    # --------------------------------------------------------

    if not os.path.isfile(
        TESSERACT_PATH
    ):

        print(
            "[ERROR] Tesseract was not found:"
        )

        print(
            TESSERACT_PATH
        )

        print()
        print(
            "Change TESSERACT_PATH at the top of WuWa.py."
        )

        input(
            "\nPress Enter to exit..."
        )

        return

    log(
        "[SYSTEM] Tesseract found."
    )

    # --------------------------------------------------------
    # Check game executable.
    # --------------------------------------------------------

    if not os.path.isfile(
        GAME_EXE
    ):

        print()
        print(
            "[ERROR] Wuthering Waves executable not found:"
        )

        print(
            GAME_EXE
        )

        print()
        print(
            "Edit GAME_EXE at the top of WuWa.py."
        )

        input(
            "\nPress Enter to exit..."
        )

        return

    log(
        "[SYSTEM] Game executable found."
    )

    # --------------------------------------------------------
    # Clear old log.
    # --------------------------------------------------------

    try:

        LOG_FILE.unlink()

    except FileNotFoundError:

        pass

    # --------------------------------------------------------
    # Start game if necessary.
    # --------------------------------------------------------

    if is_game_running():

        log(
            "[WuWa] Game is already running."
        )

    else:

        if not launch_game():

            input(
                "\nPress Enter to exit..."
            )

            return

    # --------------------------------------------------------
    # Wait for graphical window.
    # --------------------------------------------------------

    window = wait_for_window_after_launch()

    if not window:

        log("")
        log(
            "[ERROR] Could not find WuWa window."
        )

        log(
            "The game may still be starting."
        )

        input(
            "\nPress Enter to exit..."
        )

        return

    # --------------------------------------------------------
    # Start monitor.
    # --------------------------------------------------------

    log("")
    log("=" * 60)
    log(
        "[OCR] MONITORING STARTED"
    )
    log("=" * 60)
    log("")
    log(
        "[OCR] Looking for:"
    )
    log(
        f"       LOGIN  = {LOGIN_TARGET}"
    )
    log(
        "       PATCH  = Patching complete / Please restart"
    )
    log("")
    log(
        "[OCR] Entire WuWa window is scanned."
    )
    log(
        "[OCR] Screen resolution does not need to be configured."
    )
    log("")
    log(
        "[OCR] Press Ctrl+C to stop."
    )
    log("")

    # --------------------------------------------------------
    # Monitor.
    # --------------------------------------------------------

    try:

        monitor_game()

    except KeyboardInterrupt:

        print()
        print()
        log(
            "[SYSTEM] Watchdog stopped by user."
        )

    except Exception as e:

        log("")
        log(
            f"[FATAL ERROR] {e}"
        )

        log(
            "The game has NOT been intentionally closed."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
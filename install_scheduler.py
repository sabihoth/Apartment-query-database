import argparse
import plistlib
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LABEL = "com.sabi.rent-tracker"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "rent-tracker"


def build_plist(hour, minute):
    run_script = str(BASE_DIR / "run_tracker_daily.sh")
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", run_script],
        "WorkingDirectory": str(BASE_DIR),
        "RunAtLoad": False,
        "StartCalendarInterval": {
            "Hour": hour,
            "Minute": minute,
        },
        "StandardOutPath": str(LOG_DIR / "launchd.out.log"),
        "StandardErrorPath": str(LOG_DIR / "launchd.err.log"),
    }


def install_launch_agent(hour, minute):
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with PLIST_PATH.open("wb") as plist_file:
        plistlib.dump(build_plist(hour, minute), plist_file)

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False, capture_output=True)
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)

    print(f"Installed {PLIST_PATH}")
    print(f"Daily schedule: {hour:02d}:{minute:02d}")
    print(f"Tracker logs: {LOG_DIR}")


def main():
    parser = argparse.ArgumentParser(description="Install the rent tracker as a daily macOS LaunchAgent.")
    parser.add_argument("--hour", type=int, default=8, help="Hour in 24-hour time. Default: 8")
    parser.add_argument("--minute", type=int, default=0, help="Minute. Default: 0")
    args = parser.parse_args()

    if not 0 <= args.hour <= 23:
        raise ValueError("--hour must be between 0 and 23")
    if not 0 <= args.minute <= 59:
        raise ValueError("--minute must be between 0 and 59")

    install_launch_agent(args.hour, args.minute)


if __name__ == "__main__":
    main()
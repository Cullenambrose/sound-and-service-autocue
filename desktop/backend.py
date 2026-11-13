"""Frozen Studio entry point, with parent-death cleanup for the desktop shell."""
import fcntl
import os
from pathlib import Path
import sys
import threading
import time

from web.studio import main


def watch_parent():
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:
            os._exit(1)


if __name__ == "__main__":
    parent = os.environ.get("AUTOCUE_DESKTOP_PARENT")
    if parent is not None:
        if parent != str(os.getppid()):
            raise SystemExit("Desktop shell is no longer running.")
        threading.Thread(target=watch_parent, daemon=True).start()
        data = Path(sys.argv[sys.argv.index("--data") + 1])
        lock = os.open(data / ".desktop-backend.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        for attempt in range(100):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.1)
        else:
            raise RuntimeError("The previous desktop backend did not stop.")
    main()

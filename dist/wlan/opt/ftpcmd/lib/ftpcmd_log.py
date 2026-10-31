"""Private start events shared by the FTP command handlers."""

import datetime
import sys
import syslog


def log_start(command: str) -> None:
    """Record only a fixed command name, before validation or device changes."""
    try:
        timestamp = datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")
        syslog.syslog(
            syslog.LOG_LOCAL0 | syslog.LOG_INFO,
            f"ftpcmd_start command={command} time={timestamp}",
        )
    except OSError:
        # Logging must not turn a successful command into a failed FTP reply.
        pass


if __name__ == "__main__":
    log_start(sys.argv[1])

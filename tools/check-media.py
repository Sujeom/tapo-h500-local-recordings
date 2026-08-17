#!/usr/bin/env python3
"""Is the H500's media service serving, wedged, or gone? One TCP exchange.

The hub's known failure: hours after a reboot, port 8800 starts accepting a
connection and closing it before a single HTTP byte, and every download and
preview fails until the hub is rebooted. The first request of a media
session is unauthenticated by design -- the digest challenge is the reply to
it -- so this asks exactly that and reads the answer. No credentials, no
login, no lockout risk, stdlib only.

The integration runs the same check every fifteen minutes and raises a
repair notice on the wedge; this is the standalone version for a terminal.
The classifier logic mirrors api.check_media_port, whose behaviour is
pinned by tests/test_media_health.py against real sockets.

    tools/check-media.py 192.168.1.50

Exit codes: 0 healthy, 1 wedged, 2 unreachable, 3 silent.
"""
import socket
import sys

REQUEST = (
    "POST /stream HTTP/1.1\r\n"
    "Content-Type: multipart/mixed;boundary=healthcheck\r\n"
    "Connection: keep-alive\r\n"
    "Content-Length: 0\r\n"
    "\r\n"
).encode()

VERDICTS = {
    "healthy": (0, "the media service answered its digest challenge"),
    "wedged": (1, "accepted the connection, closed without one byte -- "
                  "the known wedge; a hub reboot clears it"),
    "unreachable": (2, "nothing is listening on port 8800"),
    "silent": (3, "connected but no reply -- try again before concluding"),
}


def check(host: str, port: int = 8800, timeout: float = 5.0) -> str:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return "unreachable"
    try:
        with sock:
            sock.settimeout(timeout)
            sock.sendall(REQUEST)
            data = sock.recv(1024)
    except (socket.timeout, TimeoutError):
        return "silent"
    except ConnectionError:
        return "wedged"
    return "healthy" if data else "wedged"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-3].strip())
        return 2
    verdict = check(sys.argv[1])
    code, meaning = VERDICTS[verdict]
    print(f"{verdict}: {meaning}")
    return code


if __name__ == "__main__":
    sys.exit(main())

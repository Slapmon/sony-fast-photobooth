"""Entrypoint for the dedicated camera-worker process.

Owns exactly one CameraBackend instance for the process lifetime and serves
it over a UNIX socket to camera/client.py. Runs single-threaded: libgphoto2
calls are blocking and must never share a thread with anything else
(photobooth-plan.md §3.2). See IMPLEMENTATION_PLAN.md T-1.6.
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError(
        "T-1.6: camera worker process + async client, supervision and restart"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Chunked download of a UCS archive from the BIG-IP file-transfer endpoint.

The role uses this instead of the download half of
f5networks.f5_modules.bigip_ucs_fetch. That module creates the UCS on the
device correctly but, on a large archive, its downloader writes a 0-byte file
locally and still reports success — so the play's backup verification then
fails (or, worse, would pass on an empty backup). Reproduced on a lab BIG-IP
21.1.0.2 with a ~569 MB UCS.

This downloader speaks the iControl REST file-transfer protocol directly: the
first GET sends `Content-Range: 0-<chunk-1>/0`, the device replies 200 with the
real total size in its `Content-Range` header and the first chunk of bytes;
subsequent GETs walk the file to the end. Standard library only, so it needs
nothing beyond the Python that already runs Ansible.

Credentials: the password is read from stdin (never argv or the environment).
TLS verification follows --validate-certs.

Output: one line of JSON on stdout. Exit status 0 on success, 1 on failure.
"""

import argparse
import base64
import json
import os
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _truthy(value):
    return str(value).strip().lower() in ("true", "yes", "1", "on")


def main():
    parser = argparse.ArgumentParser(description="Chunked BIG-IP UCS download")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default="443")
    parser.add_argument("--user", required=True)
    parser.add_argument("--path", required=True,
                        help="REST download path, e.g. "
                             "/mgmt/shared/file-transfer/ucs-downloads")
    parser.add_argument("--name", required=True, help="Remote UCS file name")
    parser.add_argument("--dest", required=True, help="Local destination path")
    parser.add_argument("--chunk", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--retries", type=int, default=3,
                        help="Retries per chunk on a transient error")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Per-request timeout in seconds")
    parser.add_argument("--validate-certs", default="false")
    args = parser.parse_args()

    password = sys.stdin.readline().rstrip("\n")
    if not password:
        print(json.dumps({"failed": True,
                          "msg": "no password supplied on stdin"}))
        return 1

    if args.chunk <= 0:
        print(json.dumps({"failed": True,
                          "msg": "chunk size must be positive"}))
        return 1

    url = "https://%s:%s%s/%s" % (args.host, args.port,
                                  args.path.rstrip("/"), args.name)
    auth = base64.b64encode(
        ("%s:%s" % (args.user, password)).encode("utf-8")).decode("ascii")

    if _truthy(args.validate_certs):
        context = ssl.create_default_context()
    else:
        context = ssl._create_unverified_context()

    dest_dir = os.path.dirname(args.dest)
    if dest_dir and not os.path.isdir(dest_dir):
        try:
            os.makedirs(dest_dir)
        except OSError as exc:
            print(json.dumps({"failed": True,
                              "msg": "cannot create %s: %s" % (dest_dir, exc)}))
            return 1

    start = 0
    size = 0            # unknown until the first response reports it
    written = 0
    began = time.time()

    with open(args.dest, "wb") as out:
        while True:
            end = start + args.chunk - 1
            if size and end > size - 1:
                end = size - 1
            headers = {
                "Authorization": "Basic %s" % auth,
                "Content-Type": "application/octet-stream",
                "Content-Range": "%d-%d/%d" % (start, end, size),
            }

            last_error = None
            data = b""
            for _ in range(max(1, args.retries)):
                try:
                    request = Request(url, headers=headers, method="GET")
                    response = urlopen(request, timeout=args.timeout,
                                       context=context)
                    code = response.getcode()
                    data = response.read()
                    crange = response.headers.get("Content-Range")
                    if code == 200:
                        if size == 0 and crange and "/" in crange:
                            size = int(crange.rsplit("/", 1)[-1])
                        last_error = None
                        break
                    last_error = "unexpected HTTP %s" % code
                except HTTPError as exc:
                    body = exc.read()[:200].decode("utf-8", "replace")
                    last_error = "HTTP %s: %s" % (exc.code, body)
                    if 400 <= exc.code < 500:
                        break
                except URLError as exc:
                    last_error = "connection error: %s" % exc.reason
                except OSError as exc:
                    last_error = "OS error: %s" % exc
                time.sleep(2)

            if last_error is not None:
                print(json.dumps({
                    "failed": True,
                    "msg": ("download of %s failed at byte %d of %s: %s"
                            % (args.name, start, size or "unknown", last_error)),
                    "written_bytes": written,
                }))
                return 1

            out.write(data)
            written += len(data)

            if size == 0:
                # No total reported and nothing more to read.
                if not data:
                    break
                start = written
                continue
            if written >= size:
                break
            start = written

    ok = (size == 0) or (written == size)
    if not ok:
        print(json.dumps({
            "failed": True,
            "msg": ("download of %s incomplete: wrote %d of %d bytes"
                    % (args.name, written, size)),
            "written_bytes": written,
            "total_bytes": size,
        }))
        return 1

    print(json.dumps({
        "failed": False,
        "changed": True,
        "name": args.name,
        "dest": args.dest,
        "total_bytes": size,
        "written_bytes": written,
        "seconds": round(time.time() - began, 1),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

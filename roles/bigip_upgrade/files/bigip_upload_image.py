#!/usr/bin/env python3
"""Chunked upload of a BIG-IP ISO to the iControl REST file-transfer endpoint.

The role uses this instead of f5networks.f5_modules.bigip_software_image for the
ISO upload. That module's uploader posts fixed 7 MiB chunks and, on any transient
error mid-transfer, rewinds the file to the start without resetting its byte
offset, corrupting the transfer; after three retries it fails with "Failed to
upload file too many times" and leaves restjavad holding stale chunk state that
blocks every retry until the service is restarted. This uploader sends
sequential, correctly-ranged chunks, retries each chunk in place, and stops with
a clear message and the real HTTP status on failure.

Runs on the Ansible control node (connection: local). Standard library only, so
no dependency beyond the Python that already runs Ansible.

Credentials: the password is read from stdin (never argv or the environment), so
it does not appear in `ps` output. TLS verification follows --validate-certs.

Output: a single line of JSON on stdout describing the result, so the calling
task can parse it. Exit status is 0 on success, 1 on failure.
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
    parser = argparse.ArgumentParser(description="Chunked BIG-IP ISO upload")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", default="443")
    parser.add_argument("--user", required=True)
    parser.add_argument("--path", required=True,
                        help="REST upload path, e.g. "
                             "/mgmt/cm/autodeploy/software-image-uploads")
    parser.add_argument("--file", required=True, help="Local ISO path")
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

    if not os.path.isfile(args.file):
        print(json.dumps({"failed": True,
                          "msg": "ISO not found: %s" % args.file}))
        return 1

    if args.chunk <= 0:
        print(json.dumps({"failed": True,
                          "msg": "chunk size must be positive"}))
        return 1

    size = os.path.getsize(args.file)
    name = os.path.basename(args.file)
    url = "https://%s:%s%s/%s" % (args.host, args.port,
                                  args.path.rstrip("/"), name)
    auth = base64.b64encode(
        ("%s:%s" % (args.user, password)).encode("utf-8")).decode("ascii")

    if _truthy(args.validate_certs):
        context = ssl.create_default_context()
    else:
        context = ssl._create_unverified_context()

    start = 0
    chunks = 0
    began = time.time()

    with open(args.file, "rb") as handle:
        while start < size:
            handle.seek(start)
            data = handle.read(args.chunk)
            if not data:
                break
            end = start + len(data)
            headers = {
                "Authorization": "Basic %s" % auth,
                "Content-Type": "application/octet-stream",
                "Content-Range": "%d-%d/%d" % (start, end - 1, size),
            }

            last_error = None
            for _ in range(max(1, args.retries)):
                try:
                    request = Request(url, data=data, headers=headers,
                                      method="POST")
                    response = urlopen(request, timeout=args.timeout,
                                       context=context)
                    code = response.getcode()
                    response.read()
                    if code == 200:
                        last_error = None
                        break
                    last_error = "unexpected HTTP %s" % code
                except HTTPError as exc:
                    body = exc.read()[:200].decode("utf-8", "replace")
                    last_error = "HTTP %s: %s" % (exc.code, body)
                    # 4xx are not transient: a stale/partial upload on the
                    # device (restart restjavad), auth, or a bad request.
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
                    "msg": ("upload of %s failed at byte %d of %d: %s"
                            % (name, start, size, last_error)),
                    "uploaded_bytes": start,
                    "total_bytes": size,
                }))
                return 1

            start = end
            chunks += 1

    print(json.dumps({
        "failed": False,
        "changed": True,
        "name": name,
        "total_bytes": size,
        "chunks": chunks,
        "seconds": round(time.time() - began, 1),
        "url": url,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

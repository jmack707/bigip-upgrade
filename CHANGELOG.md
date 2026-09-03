# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project follows
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-03

First stable release: HA-aware, fail-safe F5 BIG-IP software upgrades with
Ansible and the `f5networks.f5_modules` collection.

### Added

- One playbook and role with six tagged phases — `preflight`, `backup`,
  `upload`, `install`, `reboot`, `verify` — plus an explicit `rollback`
  phase. Runs one device at a time (`serial: 1`, `any_errors_fatal`).
- **Backup before change**: a UCS archive is created on the device, fetched
  to the control node, and asserted non-empty before anything is installed.
  Optional passphrase encryption and on-device cleanup.
- **Verified image**: the `.384.sig` is uploaded next to the ISO and the play
  requires the device to report the image `verified` before installing
  (`bigip_require_verified_image`).
- **Robust chunked ISO uploader**
  (`roles/bigip_upgrade/files/bigip_upload_image.py`) replacing the
  collection's `bigip_software_image` uploader, which fails on large images
  and leaves the device needing a `restjavad` restart. Configurable chunk
  size and per-chunk retry; password read from stdin (never a command line);
  TLS honouring `bigip_validate_certs`.
- **HA reboot interlock**: refuses to reboot the active member of a device
  group unless it is failed over or the interlock is explicitly overridden;
  standalone devices are exempt. Optional automatic failover with a bounded
  wait (`bigip_auto_failover`).
- **Reboot detection**: waits for the management port to close so a rejected
  reboot fails fast instead of after the reboot timeout.
- **Correctness**: exact version/build assertions after install and after
  reboot; three- and four-part TMOS versions supported (e.g. `17.1.1`,
  `21.1.0.2`); target-volume selection prefers a volume already holding a
  completed install of the target; idempotent re-runs and a
  stage-then-reboot workflow (upload skipped when the image is present).
- **Tooling and docs**: CI (yamllint, ansible-lint production profile,
  syntax check, device-free logic tests), an `ansible-builder`
  execution-environment template, an operator runbook (`docs/OPERATIONS.md`),
  and engineering notes (`docs/ENGINEERING.md`).

### Security

- Credentials sourced from Ansible Vault; the provider fact and uploads are
  `no_log`; no credentials appear on any command line; TLS verification is
  configurable and documented as required in production.

### Requirements

- Control node: ansible-core >= 2.16 (Python >= 3.10) and
  `f5networks.f5_modules` >= 1.19.0. On RHEL/Rocky 8/9 the system Python is
  3.9; use a Python 3.11 virtualenv (see the README).

### Validated

- Full standalone upgrade (upload -> install -> reboot -> verify) on a lab
  BIG-IP 21.1.0.2 VE.
- Pending a two-node lab: HA-pair automatic failover (`bigip_auto_failover`)
  and rollback ordering across a pair.

[1.0.0]: https://github.com/jmack707/bigip-upgrade/releases/tag/v1.0.0

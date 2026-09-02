# Operations Runbook: BIG-IP Software Upgrade

This runbook is for the operator executing an upgrade with this playbook.
It assumes the quick start in the [README](../README.md) has been completed
once (collection installed, vault created).

Contents

1. [Before you begin](#1-before-you-begin)
2. [Standalone device procedure](#2-standalone-device-procedure)
3. [HA pair procedure](#3-ha-pair-procedure)
4. [Rollback](#4-rollback)
5. [Re-running the play](#5-re-running-the-play)
6. [Reading the output](#6-reading-the-output)
7. [Troubleshooting](#7-troubleshooting)
8. [Logging and audit](#8-logging-and-audit)
9. [Housekeeping](#9-housekeeping)

---

## 1. Before you begin

Work through this list for every upgrade. The play checks some of these
items; the rest are yours.

| # | Check | Checked by the play? |
|---|---|---|
| 1 | Target release is supported for the platform and licensed modules (F5 release notes and compatibility matrix for your hardware/VE). | No |
| 2 | The license **service check date** is later than the date required by the target release. Reactivate the license first if not (F5 article K7727). | No |
| 3 | Any known-issue or upgrade-path constraint in the release notes (some releases require an intermediate version). | No |
| 4 | ISO and `.384.sig` downloaded from my.f5.com; the MD5 shown on the download page matches the ISO. | Existence, naming and size only |
| 5 | `vars/bigip_upgrade_vars.yml` describes exactly that ISO: `iso_image_name`, `iso_sig_name`, `expected_version` (and `expected_build` if you want the build enforced). | Yes (format), and after install (volume version) |
| 6 | Inventory order is correct. For HA pairs the **standby** member is in `bigip_primary`, the **active** member in `bigip_secondary`. | No |
| 7 | Device has free space in `/shared/images` and `/var` for the ISO and a UCS. | No |
| 8 | A previous UCS exists off-box, or the `backup` phase will run in this upgrade. | Yes if `backup` runs |
| 9 | Change window approved; rollback plan (section 4) understood; the volume currently active is noted. | Printed by the reboot phase |
| 10 | Control node can reach every management address on `bigip_server_port` (default 443). | Implicitly, in preflight |
| 11 | Vault password available; `bigip_validate_certs: true` set for production devices with valid certificates. | No |

Dry check with no device changes:

```bash
ansible-playbook upgrade_bigip.yml --tags preflight --ask-vault-pass
```

This validates input, confirms the files, prints each device's hostname,
HA mode, failover state, current version and the volume that would be used.

---

## 2. Standalone device procedure

Standalone devices report failover state `active` but sync-status mode
`standalone`; the HA interlock does not apply to them.

**Option A: everything in one run** (device reboots during the run)

```bash
ansible-playbook upgrade_bigip.yml --ask-vault-pass
```

**Option B: stage first, reboot in the window** (recommended)

```bash
# Business hours: backup, upload, install. No reboot. Traffic unaffected.
ansible-playbook upgrade_bigip.yml --tags preflight,backup,upload,install --ask-vault-pass

# Maintenance window: reboot into the staged volume and verify.
ansible-playbook upgrade_bigip.yml --tags preflight,reboot,verify --ask-vault-pass
```

The second run re-reads the volumes and finds the staged volume itself; no
volume name has to be carried between runs.

Expected wall-clock time per device: upload 2 to 10 minutes depending on the
link, install 10 to 25 minutes, reboot and readiness 5 to 15 minutes.

---

## 3. HA pair procedure

Terminology: **unit A** is the member that is *standby* before the upgrade,
**unit B** is *active*.

### 3.1 Inventory

```ini
[bigip_primary]
bigip-a.example.com ansible_host=192.0.2.10    # standby (upgraded first)

[bigip_secondary]
bigip-b.example.com ansible_host=192.0.2.11    # active
```

### 3.2 Stage both units (business hours)

```bash
ansible-playbook upgrade_bigip.yml --tags preflight,backup,upload,install --ask-vault-pass
```

Both units get a UCS backup, the image, and an installed (inactive)
volume. Nothing reboots.

### 3.3 Maintenance window, step 1: reboot unit A (standby)

```bash
ansible-playbook upgrade_bigip.yml --tags preflight,reboot,verify --limit bigip_primary --ask-vault-pass
```

The interlock confirms unit A is standby, reboots it, waits, and asserts
the new version is active. Unit B keeps serving traffic throughout.

### 3.4 Maintenance window, step 2: fail over to unit A

Move traffic to the upgraded unit. From unit B (currently active):

```
tmsh run sys failover standby
```

Or in the GUI: Device Management > Devices > (unit B) > Force to Standby.
Confirm unit A shows `ACTIVE` and services are healthy on the new version.
This is the point to pause if anything looks wrong: unit B is untouched and
can take traffic back with the same command run on unit A.

### 3.5 Maintenance window, step 3: reboot unit B

Unit B is now standby, so the interlock lets it through:

```bash
ansible-playbook upgrade_bigip.yml --tags preflight,reboot,verify --limit bigip_secondary --ask-vault-pass
```

If you prefer the play to do step 3.4 for you, run step 3.5 with
`-e bigip_auto_failover=true` instead: the play issues
`run sys failover standby` on unit B, waits for it to report standby, then
reboots it.

### 3.6 After both units are upgraded

- Both units run the same version; config sync is possible again. Do not
  sync while the versions differ.
- Verify sync status (`tmsh show cm sync-status`) and, if the device group
  is manual-sync, sync from the unit whose configuration you want to keep:
  `tmsh run cm config-sync to-group <device-group>`.
- Optionally fail back to your preferred active unit.
- Keep the old volumes for the agreed soak period; they are your rollback.

### 3.7 What the play does *not* do in HA

- It does not fail over unless `bigip_auto_failover: true`.
- It does not config-sync.
- It does not check that the peer is healthy before failing over. Confirm
  the peer's state yourself in 3.4.

---

## 4. Rollback

Each device keeps the previous release in its former boot volume. The
reboot phase prints that volume name ("Rollback target for ... is HD1.x")
before rebooting; `tmsh show sys software volume` lists it at any time.

```bash
ansible-playbook upgrade_bigip.yml --tags rollback -e rollback_volume=HD1.1 --limit bigip-a.example.com --ask-vault-pass
```

The rollback phase:

1. Confirms the volume exists, is inactive, and holds a completed install.
2. Runs the same HA interlock as an upgrade (an active member is refused
   unless `bigip_auto_failover` or `bigip_allow_active_reboot` is set).
3. Reboots into the volume, waits for the port to drop, waits for the
   device, and asserts the volume is now active.

`rollback` never runs as part of a normal upgrade; the tag is marked
`never` and must be requested explicitly. Do not combine it with
`preflight` (preflight would end the host as "already on target").

For HA pairs, roll back in the reverse order of the upgrade: make the
not-yet-rolled-back unit active first, then roll back the standby.

Configuration made *after* the upgrade on the new version is not carried
back by a reboot into the old volume. Restore it from the UCS in
`ucs_backup_dest/<hostname>/` only if needed, and only after the device is
back on the version that UCS was taken from.

---

## 5. Re-running the play

Every phase is safe to repeat.

| Situation | Behaviour |
|---|---|
| Device already on `expected_version` | Preflight ends the host with "already running"; no other phase runs for it. |
| ISO already on the device | `bigip_software_image` reports no change. |
| Volume already holds the image (same version and build) | `bigip_software_install` reports no change; the volume is re-checked. |
| Run interrupted after `install` | Re-run `--tags preflight,reboot,verify`; the staged volume is found again. |
| Run interrupted during `reboot` | Re-run `--tags preflight,verify` to wait and verify, or `--tags preflight,reboot,verify` (reboot refuses if the volume is not staged). |
| Backup re-run | A new UCS is created (filename is timestamped to the second). |

---

## 6. Reading the output

Lines to look for, per device:

```
bigip-a: hostname bigip-a.example.com, platform BIG-IP Virtual Edition, mode high-availability, failover state standby, sync status In Sync.
bigip-a is running 16.1.4 build 0.0.9 on HD1.1; target is 17.1.1.
Target volume for bigip-a: HD1.2 (inactive, will be installed).
UCS backup for bigip-a saved to ./ucs_backups/bigip-a.example.com/pre-upgrade-...ucs (1234567 bytes).
BIGIP-17.1.1-0.0.2.iso is on bigip-a (verified: yes).
bigip-a: 17.1.1 installed to HD1.2 (not yet active).
Rollback target for bigip-a is HD1.1 (use: --tags rollback -e rollback_volume=<that volume>).
bigip-a (high-availability, standby) is safe to reboot.
bigip-a is running 17.1.1 on HD1.2.
```

The final summary block:

```
device:          bigip-a.example.com (bigip-a)
active volume:   HD1.2
version/build:   17.1.1 / 0.0.2
failover state:  standby
ha mode:         high-availability
sync status:     Changes Pending (yellow)
next action:     ...
```

`sync status` after upgrading one member of a pair will normally not be
"In Sync"; that is expected until both members run the same version.

---

## 7. Troubleshooting

| Symptom (task name or message) | Cause | Action |
|---|---|---|
| `Validate required operator variables` fails | `expected_version` includes a build, or a variable is missing | Use the version only (`17.1.1`); put the build in `expected_build` if needed |
| `Validate ISO and signature files` fails | File missing, wrong path, or signature not named `<ISO>.384.sig` | Copy both files; keep F5's filenames |
| `Gather device identity and HA state` fails with 401 | Wrong credentials or user lacks Administrator role | `ansible-vault view group_vars/bigip/vault.yml`; check the user's role |
| Same task fails with certificate error | `bigip_validate_certs: true` and the device uses a self-signed certificate | Install a trusted certificate, or set `bigip_validate_certs: false` for lab devices only |
| `already running ... Nothing to do` | Device is on the target version | Expected on re-runs |
| `Create UCS on device and fetch it` times out | Large configuration | Raise `ucs_async_timeout` (up to 1800) and `bigip_api_timeout` |
| `Assert the UCS backup exists locally` fails | Fetch failed silently or `ucs_backup_dest` not writable | Check the directory permissions; re-run `--tags preflight,backup` |
| `Upload ISO signature file` fails (HTTP 4xx) | REST path rejected the file, or user lacks permission | See `bigip_sig_upload_path` in [ENGINEERING.md](ENGINEERING.md#5-assumptions-to-validate-in-the-lab); check `/var/log/restjavad.0.log` on the device |
| `Assert the uploaded image is present and verified` fails | Signature not next to the ISO, wrong signature, or a BIG-IP version that does not verify images | On the device: `tmsh show sys software image`. If the image is legitimately unverifiable, verify the ISO MD5 manually and re-run with `-e bigip_require_verified_image=false` |
| `Install software to target volume` fails with "image was not found" | ISO name on the device differs from `iso_image_name` basename | `tmsh list sys software image`; fix `iso_image_name` |
| `Assert the target volume now holds the expected version` fails | ISO and `expected_version` describe different releases | Fix `vars/bigip_upgrade_vars.yml`; the installed volume can be reused |
| `Refusing to reboot ... volume ... expected ... 'complete'` | Install phase did not run or did not finish | Run `--tags preflight,upload,install` first |
| `... is the ACTIVE member of its device group` | HA interlock | Fail the unit over (section 3.4), or `-e bigip_auto_failover=true`, or accept impact with `-e bigip_allow_active_reboot=true` |
| `Wait for the unit to leave the active state` times out | Peer cannot take over (offline, not in sync, forced offline) | Fix the peer; check `tmsh show sys failover` on both |
| `Confirm the management port stopped answering` times out | Reboot command was rejected or the device takes longer to shut down | Check `/var/log/ltm` on the device; raise `bigip_reboot_detect_timeout`; look at the "Module result" in the message |
| `Wait for BIG-IP to be ready after reboot` times out | Device did not return within `bigip_reboot_timeout` | Console/LOM access; the device may be waiting at a boot prompt or running a long first-boot (VE resize, module provisioning). Raise the timeout for slow platforms |
| `Assert the expected version is now the active volume` fails | Device booted the old volume (install incomplete, boot location not switched) | `tmsh show sys software volume`; do not continue to other devices; re-run install and reboot, or roll back |
| Vault decryption error | Wrong vault password or unencrypted file | `ansible-vault view group_vars/bigip/vault.yml` |

Useful device-side commands:

```
tmsh show sys software volume
tmsh show sys software image
tmsh show sys failover
tmsh show cm sync-status
tmsh show sys license | grep -i "service check"
```

---

## 8. Logging and audit

- Keep a log of each run: `ANSIBLE_LOG_PATH=logs/upgrade-$(date +%F).log ansible-playbook ...`
  (`logs/` is git-ignored).
- Credentials do not appear in logs: the provider fact is `no_log`, and the
  `uri` module marks `url_password` as `no_log` itself.
- Increase verbosity with `-v` for module return values; `-e debug_mode=true`
  prints the gathered volume and image lists.
- The UCS archives in `ucs_backup_dest` contain the device configuration
  including private keys (encrypted with `ucs_encryption_password` if set).
  Store them accordingly.

---

## 9. Housekeeping

- **Old images** stay in `/shared/images`. Remove them once the release is
  accepted: `tmsh delete sys software image <name>` or, with Ansible,
  `f5networks.f5_modules.bigip_software_image` with `state: absent`.
- **Old volumes** are your rollback; keep them for the soak period, then
  they are simply reused by the next upgrade.
- **UCS on the device** (`/var/local/ucs`): set `ucs_remove_from_device: true`
  to delete each archive after it is fetched, or prune manually.
- **UCS on the control node**: apply your retention policy to
  `ucs_backup_dest`.

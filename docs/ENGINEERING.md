# Engineering Notes: bigip_upgrade role

For engineers maintaining or extending the role. Explains how the play is
built, which module behaviours it relies on and where each was verified,
what still has to be validated in a lab, and the known limitations.

Contents

1. [Architecture](#1-architecture)
2. [Execution model](#2-execution-model)
3. [Phase design](#3-phase-design)
4. [Module behaviour verified from the collection source](#4-module-behaviour-verified-from-the-collection-source)
5. [Assumptions to validate in the lab](#5-assumptions-to-validate-in-the-lab)
6. [Security](#6-security)
7. [Facts set by the role](#7-facts-set-by-the-role)
8. [Known limitations](#8-known-limitations)
9. [Testing and CI](#9-testing-and-ci)
10. [Decision record](#10-decision-record)

---

## 1. Architecture

```
upgrade_bigip.yml                 play: hosts bigip, serial 1, connection local
  └── role bigip_upgrade
        tasks/main.yml            provider fact (always) + tagged imports
          ├── preflight.yml       [preflight]  validate, identify
          ├── version_check.yml   [preflight]  already-on-target, target_volume
          ├── backup.yml          [backup]
          ├── upload.yml          [upload]
          ├── install.yml         [install]
          ├── reboot.yml          [reboot]  ──► ha_interlock.yml ──► reboot_into_volume.yml
          ├── verify.yml          [verify]
          └── rollback.yml        [rollback, never] ──► ha_interlock.yml ──► reboot_into_volume.yml
```

All device interaction goes through `f5networks.f5_modules` over iControl
REST, except the signature upload (`ansible.builtin.uri`, because the
image module only accepts ISOs). Every task is `delegate_to: localhost`;
nothing runs on the BIG-IP itself, so appliance mode (no bash) is
supported: the only tmsh commands issued are `save sys config`,
`run sys failover standby` and `reboot volume <x>`.

Shared task files (`ha_interlock.yml`, `reboot_into_volume.yml`) are
pulled in with `import_tasks` so they inherit the caller's tags. Each
re-gathers the state it needs; nothing shared depends on facts from an
earlier phase except `target_volume` (documented and asserted).

## 2. Execution model

| Setting | Value | Why |
|---|---|---|
| `serial` | 1 | Never two devices mid-upgrade; an HA pair always has one untouched member |
| `any_errors_fatal` | true | The first failure aborts the whole run, including hosts not started yet |
| `max_fail_percentage` | 0 | Belt and braces with the above |
| `gather_facts` | false | No SSH to devices; facts are gathered through the API |
| `connection` | local | All modules run on the control node |
| `meta: end_host` | in version_check | A host already on target leaves the play cleanly; other hosts continue |

`--tags` selects phases. `preflight` is required with `install` or
`reboot` because it sets `target_volume`; both phases assert this with an
actionable message. `rollback` is tagged `never` so it runs only when
named explicitly.

## 3. Phase design

### preflight

- Asserts operator input shape: `expected_version` must be
  `major.minor.point` with no build; `expected_build`, if set, likewise.
- `stat` on the ISO and signature with `get_checksum: false` (the default
  would SHA-1 a multi-GB file on every run).
- Signature must be named `<ISO basename>.384.sig` and be under 1 MiB
  (the upload is a single chunk; iControl REST file transfer chunks are
  limited to 1 MiB).
- Gathers `devices` and `sync-status`; records `bigip_self_device`,
  `bigip_device_hostname`, `bigip_ha_mode`.

### version_check

- Exact string comparison of the active volume's `version` with
  `expected_version` (plus `build` if `expected_build` is set). The
  earlier regex `search` treated dots as wildcards; an anchored `match`
  still accepted `17.1.10` for `17.1.1`. Equality is what the README
  promises.
- Target volume selection, in order:
  1. an **inactive** volume with `version == expected_version` and
     `status == complete` (a staged install from an earlier run);
  2. the highest-named inactive volume (unchanged from the original role);
  3. `bigip_fallback_volume`, which the install module creates.
  Rule 1 fixes a real defect: with two inactive volumes, the original
  "highest inactive" rule could pick a different volume in the
  maintenance-window run than the one the image had been installed to,
  and reboot into it.

### backup

- `save sys config` first (tunable) so the UCS captures unsaved running
  configuration.
- UCS name is timestamped to the second; `bigip_ucs_fetch` reuses an
  existing file of the same name on the device, so a date-only name
  could silently fetch a stale archive on a same-day re-run.
- `stat` and assert the local file exists and is non-empty. The module
  cannot checksum a UCS on the device (module notes), so existence and
  size are the strongest check available.
- Optional encryption passphrase and optional removal from the device.

### upload

- Signature first (via `uri`), then ISO, so the signature is already in
  `/shared/images` when the BIG-IP parses the ISO. Both go to
  `bigip_sig_upload_path` (`software-image-uploads`), which stores into
  `/shared/images`.
- The ISO is uploaded by `files/bigip_upload_image.py`, **not** by
  `bigip_software_image`. The collection's uploader posts fixed 7 MiB
  chunks and, on any mid-stream error, rewinds the file without resetting
  its byte offset, corrupting the transfer; after three retries it fails
  with "Failed to upload file too many times" and leaves stale chunk state
  in restjavad that blocks every retry until the service is restarted.
  This was reproduced on a lab 21.1.0.2 device under both ansible-core 2.14
  and 2.16 (see the decision record). The script sends correctly-ranged
  chunks (default 10 MiB, `bigip_upload_chunk_size`), retries each chunk in
  place (`bigip_upload_retries`), reads the password from stdin, and honours
  `bigip_validate_certs`. It exits with one JSON line the task parses.
- The upload is skipped when the image is already present, so re-runs and
  the staged-then-reboot workflow do not re-send several GB.
- After upload the play polls `software-images` until the ISO registers,
  then asserts it is present and `verified` is yes/true, unless
  `bigip_require_verified_image` is false. A raw upload to
  `software-image-uploads` registers and verifies exactly as the module's
  did (confirmed in the lab: `verified: yes` with no ownership step).

### install

- `bigip_software_install` with `state: installed` (install, mark
  bootable, no reboot); `image` is the ISO basename because the module
  matches against image names on the device, not paths.
- Re-gathers volumes and asserts the target volume holds
  `expected_version` with `status: complete`. This catches an ISO /
  `expected_version` mismatch before any reboot.

### reboot

1. Assert `target_volume` is set.
2. Re-gather volumes; assert the target volume holds a completed install
   of `expected_version` (never reboot into an empty or wrong volume).
3. Print the currently active volume as the rollback target.
4. `ha_interlock.yml`.
5. `reboot_into_volume.yml`.

### ha_interlock

- Gathers `devices` + `sync-status` fresh (state may have changed since
  preflight).
- If mode is `high-availability` and the self device is `active`:
  - with `bigip_auto_failover`: `run sys failover standby`, then poll
    `devices` until the self device is no longer active
    (`bigip_failover_wait_retries` x `bigip_failover_wait_delay`);
  - assert not active, unless `bigip_allow_active_reboot`.
- Standalone devices report `active` too; the mode check exempts them.
  (A previous branch assumed standalone devices report standby/offline;
  that would have blocked every standalone upgrade.)

### reboot_into_volume

- `bigip_command` `reboot volume <vol>` with `failed_when: false`. The
  reboot drops the REST session, so the module often returns an error on
  success; parsing error text (as one earlier branch did) is fragile.
- Instead, `wait_for` `state: stopped` on the management port with
  `bigip_reboot_detect_timeout` proves the reboot started. A rejected
  command (typo in the volume name, insufficient role) therefore fails
  here, not after `bigip_reboot_timeout` in verify.

### verify

- `bigip_wait` with `delay`/`timeout`; returns when the REST API answers
  and module provisioning is not running.
- Gathers volumes, devices, sync-status; asserts active version (and
  build if set); prints a fixed-format summary.

### rollback

Self-contained (does not use preflight facts): asserts the requested
volume exists, is inactive and complete; interlock; reboot; wait; assert
the requested volume is active.

## 4. Module behaviour verified from the collection source

Checked against `f5networks.f5_modules` (collection source at the
1.44.0-devel tree; the behaviours below are long-standing). File paths
are under `plugins/modules/` unless noted.

| Behaviour the role relies on | Where verified |
|---|---|
| `software_volumes[].active` is the **string** `'yes'`/`'no'`, not a bool (docs say bool) | `bigip_device_info.py`, `SoftwareVolumesParameters.active` |
| `software_volumes[].version` excludes the build; `build`, `status` (`complete`) exist | `bigip_device_info.py` RETURN, `software_volumes` |
| `devices[].self` is `'yes'`/`'no'` (`flatten_boolean`); `devices[].failover_state` is a plain string | `bigip_device_info.py`, devices parameters, `module_utils/common.py::flatten_boolean` |
| `sync_status` is a **list with one dict** (`mode`, `status`, `color`, `recommended_action`) | `bigip_device_info.py`, `SyncStatusFactManager._exec_module` |
| `software_images[].verified` is passed through from the REST resource | `bigip_device_info.py`, software images `returnables` |
| ISO upload endpoint is `/mgmt/cm/autodeploy/software-image-uploads`, chunked with `Content-Range`; image lands in `/shared/images/<name>` | `bigip_software_image.py::create_on_device`, `_set_mode_and_ownership`; `module_utils/icontrol.py::upload_file` |
| `bigip_software_image` is idempotent (no upload when the image exists unless `force`) | `bigip_software_image.py::present/update` |
| `bigip_software_install` `state: installed` = install + mark bootable, no reboot; creates the volume if missing (`create-volume`); idempotent when version and build already match; polls until `status == complete`, fails on `failed`; `image` must match an image **name** on the device | `bigip_software_install.py::exists`, `software_installation_exists`, `update`, `wait_for_software_install_on_device` |
| `bigip_software_install` `state: activated` waits for the reboot with **no timeout** (why the role does not use it for the reboot) | `bigip_software_install.py::wait_for_device_reboot` |
| `bigip_ucs_fetch` `dest` may be a file path or a directory; `async_timeout` range 150 to 1800; `encryption_password` is `no_log`; a UCS cannot be checksummed on-device | `bigip_ucs_fetch.py` argspec, `fulldest`, module notes |
| `bigip_ucs` `state: absent` removes a UCS from the device | `bigip_ucs.py` argspec |
| `bigip_command` accepts `warn: false`; only tmsh commands are supported; module is documented as a last resort | `bigip_command.py` argspec and DOCUMENTATION |
| `bigip_wait` polls the REST endpoint and module provisioning (mprov) before returning | `bigip_wait.py::wait_for_device`, `_rest_endpoints_ready`, `_is_mprov_running_on_device` |
| Provider suboptions: `server_port` (int, default 443), `validate_certs`, `timeout` | `plugins/doc_fragments/f5.py` |
| Collection requires ansible-core >= 2.16; BIG-IP >= 12; admin user | `meta/runtime.yml`, `doc_fragments/f5.py` notes |
| Idempotency fix for `bigip_software_install` when a volume holds a different version (basis for the `>=1.19.0` floor) | `CHANGELOG.rst`, v1.19.0 |

Ansible-side facts verified with `ansible-doc`:

- `wait_for` `state: stopped` waits until the port is **closed**.
- `now(utc=true, fmt=...)` is available as a Jinja global.

## 5. Assumptions to validate in the lab

These could not be verified against product documentation from the
development environment (F5 documentation hosts were not reachable). Each is
isolated behind a variable or an assertion so a wrong assumption fails the
run visibly rather than silently. A full standalone upgrade (upload →
install → reboot → verify) was validated end to end on a lab **BIG-IP
21.1.0.2** device (control node Rocky Linux, ansible-core 2.16, f5_modules
1.43): A1-A5 confirmed. A6 (multi-traffic-group HA failover) and rollback on
an HA pair still need a two-node lab.

| # | Assumption | Status | If wrong you will see | Mitigation / variable |
|---|---|---|---|---|
| A1 | `software-image-uploads` accepts a `.384.sig` filename and stores it in `/shared/images` | **Confirmed (21.1.0.2)** | `Upload ISO signature file` fails with an HTTP error | `bigip_sig_upload_path` |
| A2 | BIG-IP reports `verified: yes` for an ISO whose `.384.sig` is present, with no ownership step | **Confirmed (21.1.0.2)** | `Assert the uploaded image is present and verified` fails | `bigip_require_verified_image: false` after manual check; `tmsh show sys software image` |
| A3 | `tmsh reboot volume <name>` via `bigip_command` reboots into that volume | **Confirmed (21.1.0.2)** — stdout "The system will be rebooted momentarily", booted HD1.2 | `Confirm the management port stopped answering` fails with the module result | Alternative: `bigip_software_install` `state: activated` (module-native, waits without timeout) |
| A4 | A standalone device reports sync-status `mode: standalone`, a member `high-availability` | **Confirmed for standalone** (lab unit is `standalone`, reports `active`; interlock exempts it). HA member side still to check | Interlock refuses a standalone device, or lets an active HA member through | `bigip_allow_active_reboot`; check `tmsh show cm sync-status` |
| A5 | The management port closes within `bigip_reboot_detect_timeout` (300 s) on your platform | **Confirmed (21.1.0.2 VE)** | False failure at `Confirm the management port stopped answering` | Raise the timeout, or `0` to disable |
| A6 | `run sys failover standby` without a traffic-group argument fails over all traffic groups | To validate (needs an HA pair) | With several traffic groups some stay active and the wait times out | Fail over manually, or loop per traffic-group in `ha_interlock.yml` |

Validated upload behaviour on the lab device (record for the next platform):

- A clean chunked upload of a 3.7 GB ISO at 10 MiB chunks completed in one
  pass (355 chunks, ~210 s) and the device reported `verified: yes`.
- The collection's `bigip_software_image` failed the same upload with
  "Failed to upload file too many times" on both ansible-core 2.14 and 2.16,
  which is why the role uses `files/bigip_upload_image.py` instead.
- An interrupted upload leaves stale chunk state in restjavad
  (`Received chunk for previously used offset ...`) that a `bigstart restart
  restjavad` clears; deleting the tmp file alone does not.

```bash
# revalidate on a new platform:
ansible-playbook upgrade_bigip.yml --tags preflight,upload -e debug_mode=true --limit <lab-device>
# then on the device:
tmsh show sys software image      # 'verified yes' for the uploaded ISO
ls -l /shared/images/             # ISO and .384.sig side by side
```

### Control-node requirement (learned in the lab)

The collection requires **ansible-core >= 2.16**, which needs **control-node
Python >= 3.10**. On RHEL/Rocky 8/9 the system Python is 3.9 and its
ansible-core tops out at 2.15, so a `python3.11 -m venv` (or newer) is
required; running under the stock 2.14 produced the `ansible.netcommon does
not support Ansible version 2.14.18` warning and unsupported behaviour. This
is documented in the README requirements.

## 6. Security

- Credentials come from Ansible Vault (`vault_bigip_username`/`_password`)
  and are passed to modules as parameters. The `provider` fact is set
  with `no_log: true` so it never appears in `-v` output. `uri` marks
  `url_password` as `no_log` in its own argspec.
- The original role uploaded the signature with `curl -u user:pass`;
  the credentials were visible in the control node's process list to any
  local user, and `-k` disabled TLS verification unconditionally. Both
  review branches replaced it with `uri`; this branch keeps that.
- `bigip_validate_certs` defaults to `false` for lab convenience and is
  documented as **must be true in production**. Consider flipping the
  default in `group_vars` for production inventories.
- `host_key_checking = False` was removed from `ansible.cfg`; nothing in
  this play uses SSH.
- `.gitignore` excludes the vault, ISOs, signatures, UCS archives and logs.
- UCS archives contain private keys. `ucs_encryption_password` is
  available and should live in the vault.

## 7. Facts set by the role

| Fact | Set in | Used by |
|---|---|---|
| `provider` (no_log) | main | every F5 module |
| `bigip_mgmt_host` | main | uri upload, wait_for |
| `iso_stat`, `iso_sig_stat` | preflight | upload (Content-Range) |
| `device_info`, `bigip_self_device`, `bigip_ha_mode`, `bigip_device_hostname` | preflight | backup (folder/filename), output |
| `software_volumes_info`, `active_volume`, `already_on_target`, `staged_volumes`, `available_volumes`, `target_volume` | version_check | install, reboot |
| `ucs_filename`, `ucs_local_dir`, `ucs_backup`, `ucs_local` | backup | backup |
| `image_info_pre`, `iso_already_present`, `sig_upload`, `iso_upload`, `image_info` | upload | upload |
| `post_install_info` | install | install |
| `pre_reboot_info`, `bigip_boot_volume` | reboot | reboot_into_volume |
| `ha_info`, `ha_info_post`, `bigip_failover_state` | ha_interlock | ha_interlock |
| `reboot_result` | reboot_into_volume | detection message |
| `post_upgrade_info`, `post_active_volume`, `post_self_device`, `post_sync` | verify | summary |
| `rollback_info`, `post_rollback_info` | rollback | rollback |

Names without a `bigip_upgrade_` prefix are kept for continuity with the
original role; ansible-lint's `var-naming[no-role-prefix]` is skipped in
`.ansible-lint` for that reason.

## 8. Known limitations

- **vCMP guests** are not handled (`bigip_software_install` `type: vcmp`
  and `block_device_image` would be needed; the ISO must be on the host).
- **Hotfix ISOs** (`Hotfix-BIGIP-...`) are not distinguished from base
  images; `expected_version` logic assumes a base image.
- **Signature over 1 MiB** would need a chunked upload; preflight rejects
  it rather than implementing chunking.
- **Multiple traffic groups**: see A6.
- **No license, disk-space or release-note checks**; see the operator
  checklist in OPERATIONS.md. A `/shared` disk-space check would need `df`
  over the bash endpoint, which appliance mode disables, so it is left as a
  documented manual step rather than an unreliable task.
- **Interrupted ISO upload** leaves stale chunk state in restjavad; the
  upload task's failure message says to clear it and `bigstart restart
  restjavad`. The role cannot restart a device service itself (appliance
  mode), so recovery is a one-line operator step.
- **No config-sync** after both members are upgraded; deliberate.
- **`bigip_reboot_timeout` is one value** for all platforms; slow
  hardware or first-boot provisioning may need more.

## 9. Testing and CI

Local:

```bash
yamllint --strict .
ansible-lint                          # .ansible-lint: production profile
ansible-playbook upgrade_bigip.yml --syntax-check -e bigip_username=x -e bigip_password=x
ansible-playbook tests/logic_test.yml # selection/assert logic against canned data
```

`tests/logic_test.yml` mirrors the Jinja expressions in `preflight.yml`,
`version_check.yml`, `ha_interlock.yml` and `upload.yml` and checks:
input validation, exact version matching, staged-volume preference,
fallback volume, HA interlock outcomes and the verified-image predicate.
Keep it in sync when those expressions change.

GitHub Actions (`.github/workflows/lint.yml`) runs all four on push to
`main` and on pull requests. It installs the collection from Galaxy, so
`ansible-lint` resolves the F5 modules there.

There is no automated end-to-end test; the lab validation in section 5
is the acceptance test before a customer rollout.

## 10. Decision record

This branch consolidates two earlier review branches
(`claude/code-review-ruqhyo`, open as PR #1, and
`claude/code-review-improvements-94103w`) and fixes defects found while
reviewing them against the collection source.

| Topic | Earlier branches | This branch | Reason |
|---|---|---|---|
| Version comparison | regex `match` + `regex_escape` | exact equality | `match` still accepts `17.1.10` for `17.1.1` |
| Reboot error handling | ruqhyo: `failed_when` on error-text substrings; improvements: `failed_when: false` | `failed_when: false` + port-down detection | Text matching is fragile; detection proves the reboot |
| HA interlock | improvements: parse `show sys failover` text; standalone assumed to report standby | `devices[].failover_state` + `sync_status[].mode` | Structured fields; standalone devices report `active` |
| Target volume | highest inactive volume | staged volume first, then highest inactive | Second run could reboot into the wrong volume |
| Signature verification | added then removed ("device verifies at install") | upload sig first, assert `verified` (fail-closed, tunable) | Whether install refuses unverified images is version-dependent; make it explicit |
| Signature upload path | `file-transfer/uploads` (stores in `/var/config/rest/downloads`) | `software-image-uploads` (stores in `/shared/images`) | The verifier looks in `/shared/images`; confirmed A1 |
| ISO upload | `bigip_software_image` module | `files/bigip_upload_image.py` chunked uploader | Module fails with "too many times" on lab 21.1.0.2 (2.14 and 2.16) and leaves restjavad needing a restart; the script uploads cleanly |
| UCS download | `bigip_ucs_fetch` create-and-fetch | `only_create_file` create + `files/bigip_download_ucs.py` chunked download | Module created the UCS but wrote a 0-byte file locally on a large archive (lab 21.1.0.2, ~569 MB); the endpoint serves fine over a chunked GET capped at 1 MiB/chunk |
| `stat` of the ISO | default (SHA-1 the ISO) | `get_checksum: false` | Minutes saved per run |
| Install image argument | `iso_image_name` (path) | `basename` | Module matches image names, so absolute paths now work |
| UCS filename | date only | timestamp to the second | Same-day re-run fetched the old archive |
| Backup order | backup before already-on-target check | after | No pointless UCS for hosts that are skipped |
| Rollback | none | `rollback` tag with interlock and verification | Operators need a tested path back |
| Unused `vars/main.yml` (`software_volume_prefix`) | kept | removed | Dead code |
| Lint / CI | improvements branch added | kept, plus syntax check and logic tests | |

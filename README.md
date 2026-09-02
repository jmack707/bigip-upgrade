# BIG-IP Software Upgrade Automation

Automated, HA-aware F5 BIG-IP software upgrades with Ansible and the
`f5networks.f5_modules` collection.

One playbook, one role, six tagged phases. Every phase checks its
preconditions and stops the run at the first problem, so a device is never
rebooted into a volume that does not hold the expected release, and the
active member of an HA pair is never rebooted by accident.

| Document | Audience | Content |
|---|---|---|
| This README | Everyone | What it does, requirements, quick start, variables |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Operators | Runbook: pre-checks, standalone and HA procedures, rollback, troubleshooting |
| [docs/ENGINEERING.md](docs/ENGINEERING.md) | Engineers | Design, module behaviour verified from source, assumptions to validate, limitations |

---

## What the play guarantees

- **Backup before change.** A UCS archive is created on the device, fetched
  to the control node and checked to be non-empty before anything is
  uploaded or installed.
- **Verified image.** The ISO signature (`.384.sig`) is uploaded next to the
  ISO and the play requires the BIG-IP to report the image as *verified*
  before installing (fail-closed; can be relaxed per run).
- **Right volume, right release.** The reboot phase re-reads the software
  volumes and refuses to reboot unless the target volume holds a *completed*
  install of `expected_version`. A staged volume from an earlier run is
  found again automatically.
- **HA interlock.** Before any reboot the play reads the device's failover
  state. The **active** member of a device group is not rebooted unless the
  operator asked for automatic failover or explicitly accepted the impact.
  Standalone devices are exempt.
- **Reboot proven, not assumed.** After `reboot volume`, the play waits for
  the management port to go down. A rejected reboot command fails the run
  immediately instead of after a long wait.
- **One device at a time, stop on first failure.** `serial: 1` and
  `any_errors_fatal: true`.
- **Credentials never on a command line.** Provider facts are `no_log`; the
  signature upload uses the `uri` module, not `curl`.
- **Explicit rollback.** `--tags rollback -e rollback_volume=HD1.1` reboots
  into a previous volume with the same interlock and verification.

---

## Repository layout

```
bigip-upgrade/
├── upgrade_bigip.yml                 # Playbook (serial: 1, any_errors_fatal)
├── ansible.cfg
├── ansible-navigator.yml             # Execution-environment runner config
├── requirements.yml                  # Collection dependency (f5networks.f5_modules)
├── inventory/hosts.ini               # bigip_primary / bigip_secondary groups
├── vars/bigip_upgrade_vars.yml       # OPERATOR: ISO, signature, expected version
├── group_vars/bigip/vault.yml.example# Credentials template (encrypt the copy)
├── roles/bigip_upgrade/
│   ├── defaults/main.yml             # Tunables (timeouts, HA behaviour, backup)
│   └── tasks/
│       ├── main.yml                  # Orchestrator: phases and tags
│       ├── preflight.yml             # Input/file validation, device identity
│       ├── version_check.yml         # Already-on-target check, volume selection
│       ├── backup.yml                # save sys config, UCS create/fetch/verify
│       ├── upload.yml                # Signature, ISO, verified-image check
│       ├── install.yml               # Install to target volume, confirm version
│       ├── reboot.yml                # Staged-volume check, HA interlock, reboot
│       ├── ha_interlock.yml          # Shared: failover state check / auto failover
│       ├── reboot_into_volume.yml    # Shared: reboot + prove it started
│       ├── verify.yml                # Wait, assert active version, summary
│       └── rollback.yml              # Explicit rollback (tag: rollback)
├── tests/logic_test.yml              # Device-free tests of the selection logic
├── ee/                               # ansible-builder execution-environment template
└── docs/                             # OPERATIONS.md, ENGINEERING.md
```

---

## Requirements

**Control node**

- Ansible core 2.16 or later (required by the collection).
- Python packages used by the collection: see `ee/requirements.txt`.
- Collection: `ansible-galaxy collection install -r requirements.yml`
  (floor 1.19.0; pin an exact version for production).
- The BIG-IP ISO **and** its `.384.sig` file, downloaded from
  [my.f5.com](https://my.f5.com/), reachable from the playbook directory.
- HTTPS access to each device's management address and port
  (default 443).

**BIG-IP**

- Software version 12 or later (collection requirement).
- A user with the Administrator role for iControl REST.
- Free space in `/shared/images` for the ISO and an inactive boot volume
  (or room to create one; see `bigip_fallback_volume`).

---

## Quick start

### 1. Install the collection

```bash
ansible-galaxy collection install -r requirements.yml
```

### 2. Describe the release

Edit `vars/bigip_upgrade_vars.yml`:

```yaml
iso_image_name: "BIGIP-17.1.1-0.0.2.iso"
iso_sig_name: "BIGIP-17.1.1-0.0.2.iso.384.sig"
expected_version: "17.1.1"      # version only, no build
# expected_build: "0.0.2"       # optional exact build
```

Place both files in the playbook directory (or use absolute paths).
The signature must be named `<ISO filename>.384.sig` exactly.

### 3. Inventory

Edit `inventory/hosts.ini`. Hosts are processed in order, all of
`bigip_primary` before `bigip_secondary`. For an HA pair, put the
**standby** member first:

```ini
[bigip_primary]
bigip-a.example.com ansible_host=192.0.2.10

[bigip_secondary]
bigip-b.example.com ansible_host=192.0.2.11
```

### 4. Credentials

```bash
cp group_vars/bigip/vault.yml.example group_vars/bigip/vault.yml
$EDITOR group_vars/bigip/vault.yml          # set vault_bigip_username/password
ansible-vault encrypt group_vars/bigip/vault.yml
```

`vault.yml` is git-ignored; only the `.example` file is tracked.

### 5. Run

```bash
# Everything, one device at a time
ansible-playbook upgrade_bigip.yml --ask-vault-pass

# Recommended: stage first, reboot in the maintenance window
ansible-playbook upgrade_bigip.yml --tags preflight,backup,upload,install --ask-vault-pass
ansible-playbook upgrade_bigip.yml --tags preflight,reboot,verify --ask-vault-pass
```

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for the full standalone and
HA procedures.

---

## Phases and tags

| Tag | What it does | Changes the device? |
|---|---|---|
| `preflight` | Validates variables and the ISO/signature files, gathers hostname and HA state, ends the host if already on `expected_version`, selects the target volume | No |
| `backup` | `save sys config`, creates a UCS on the device, fetches it to `ucs_backup_dest/<hostname>/`, verifies it is non-empty | Writes a UCS |
| `upload` | Uploads the signature, then the ISO; requires the device to report the image as verified | Adds files to `/shared/images` |
| `install` | Installs the image to the target volume (no reboot); confirms the volume now holds `expected_version` | Yes |
| `reboot` | Confirms the target volume is staged, runs the HA interlock, reboots into the volume, waits for the port to go down | Reboot |
| `verify` | Waits for the device, asserts `expected_version` is active, prints a summary | No |
| `rollback` | Only with `--tags rollback`: reboots into `rollback_volume` and verifies | Reboot |

The provider task is tagged `always`. `preflight` is required in any run
that includes `install` or `reboot` (it selects the target volume).

---

## Variables

**Operator variables** (`vars/bigip_upgrade_vars.yml`)

| Variable | Description |
|---|---|
| `iso_image_name` | ISO path on the control node (relative to the playbook dir or absolute) |
| `iso_sig_name` | Signature path; basename must be `<ISO basename>.384.sig` |
| `expected_version` | Version that must be active after the reboot, e.g. `17.1.1` |
| `expected_build` | Optional exact build, e.g. `0.0.2` (default: any build) |

**Tunables** (`roles/bigip_upgrade/defaults/main.yml`; override in
`group_vars`, `host_vars` or `-e`)

| Variable | Default | Description |
|---|---|---|
| `bigip_validate_certs` | `false` | Validate the management TLS certificate. Set `true` in production |
| `bigip_server_port` | `443` | iControl REST port |
| `bigip_api_timeout` | `120` | Per-request timeout for the F5 modules (seconds) |
| `bigip_upload_wait_timeout` | `300` | Wait for the device to be ready before upload |
| `bigip_sig_upload_path` | `/mgmt/cm/autodeploy/software-image-uploads` | REST path the `.384.sig` is uploaded to (stores in `/shared/images`) |
| `bigip_require_verified_image` | `true` | Fail unless the device reports the image as verified |
| `bigip_fallback_volume` | `HD1.2` | Volume to create when the device has no inactive volume |
| `bigip_auto_failover` | `false` | Run `sys failover standby` on an active HA member before rebooting it |
| `bigip_allow_active_reboot` | `false` | Bypass the HA interlock (traffic impact) |
| `bigip_failover_wait_retries` / `_delay` | `12` / `5` | How long to wait for the unit to leave `active` after auto failover |
| `bigip_reboot_detect_timeout` | `300` | Seconds to wait for the management port to go down after the reboot; `0` disables |
| `bigip_reboot_delay` | `120` | Seconds before polling for the device after the reboot |
| `bigip_reboot_timeout` | `2400` | Maximum seconds to wait for the device to return |
| `ucs_backup_dest` | `./ucs_backups` | Local backup directory |
| `ucs_async_timeout` | `600` | Wait for the async UCS create (150 to 1800) |
| `ucs_save_config_first` | `true` | Run `save sys config` before creating the UCS |
| `ucs_encryption_password` | `""` | Encrypt the UCS with this passphrase (keep in the vault) |
| `ucs_remove_from_device` | `false` | Delete the UCS from the device after it is fetched |
| `debug_mode` | `false` | Print gathered volume/device data |

---

## HA pairs in one paragraph

Upgrade the standby member first, verify, fail over so the upgraded unit
carries traffic, then upgrade the other member. The play enforces the
"never reboot the active member" part; the failover between the two units
is a deliberate operator step (or `bigip_auto_failover: true`). Do not
config-sync between members running different versions; sync once both are
upgraded. Full procedure: [docs/OPERATIONS.md](docs/OPERATIONS.md#3-ha-pair-procedure).

---

## Development

```bash
pip install "ansible-core>=2.16" ansible-lint yamllint
ansible-galaxy collection install -r requirements.yml
yamllint --strict .
ansible-lint                       # production profile
ansible-playbook upgrade_bigip.yml --syntax-check -e bigip_username=x -e bigip_password=x
ansible-playbook tests/logic_test.yml
```

The same checks run in GitHub Actions on every push and pull request
(`.github/workflows/lint.yml`).

---

## References

- [F5 Ansible collection documentation](https://clouddocs.f5.com/products/orchestration/ansible/devel/)
- [f5networks.f5_modules module index](https://docs.ansible.com/projects/ansible/latest/collections/f5networks/f5_modules/index.html)
- [K24341140: verifying BIG-IP software images with .sig files](https://my.f5.com/manage/s/article/K24341140)
- [K84554955: BIG-IP upgrade guide](https://my.f5.com/manage/s/article/K84554955)
- [K13132: backing up and restoring BIG-IP configuration files with a UCS archive](https://my.f5.com/manage/s/article/K13132)
- [K7727: license activation may be required before a software upgrade](https://my.f5.com/manage/s/article/K7727)
- [Ansible Vault](https://docs.ansible.com/ansible/latest/vault_guide/index.html)
- [ansible-builder](https://ansible-builder.readthedocs.io/)

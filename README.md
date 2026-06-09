# Ansible BIG-IP Upgrade Automation

Automated, HA-safe BIG-IP software upgrades using the `f5networks.f5_modules` Ansible collection.

---

## What's in this repo

```
bigip-upgrade/
├── ansible.cfg
├── ansible-navigator.yml
├── upgrade_bigip.yml                   # Main playbook
├── vars/
│   └── bigip_upgrade_vars.yml          # Operator-managed: ISO filename, sig file, target version
├── inventory/
│   └── hosts.ini                       # Canonical inventory (primary/secondary groups)
├── group_vars/
│   └── bigip/
│       ├── vault.yml                   # Ansible Vault — credentials (gitignored)
│       └── vault.yml.example           # Template — tracked in git
└── roles/
    └── bigip_upgrade/                  # Sealed — do not edit
        ├── defaults/main.yml           # Tunable defaults (validate_certs, timeouts, etc.)
        ├── vars/main.yml               # Role-internal constants only
        ├── handlers/main.yml           # Placeholder
        └── tasks/
            ├── main.yml                # Orchestrator — imports phase files, defines tags
            ├── preflight.yml           # ISO/sig validation, hostname gather
            ├── backup.yml              # UCS backup — create on device, fetch locally
            ├── version_check.yml       # Already-on-target check, volume selection
            ├── upload.yml              # Upload ISO and sig file to device
            ├── install.yml             # Install image to target volume (no reboot)
            ├── reboot.yml              # Reboot into target volume
            └── verify.yml             # Post-reboot wait and version assertion
```

---

## Requirements

- Ansible 2.16+
- F5 collection: `ansible-galaxy collection install f5networks.f5_modules`
- BIG-IP ISO and `.384.sig` file on the Ansible control node (filenames set in `vars/bigip_upgrade_vars.yml`)

---

## Quick start

### 1. Set your target version, ISO, and sig file

Edit `vars/bigip_upgrade_vars.yml`:

```yaml
iso_image_name: "BIGIP-21.1.0-0.0.38.iso"
iso_sig_name: "BIGIP-21.1.0-0.0.38.iso.384.sig"
expected_version: "21.1.0"
```

Both files must be present on the control node in the playbook directory:

```bash
scp BIGIP-21.1.0-0.0.38.iso BIGIP-21.1.0-0.0.38.iso.384.sig \
    jmack@vegas:~/Downloads/f5/bigip-upgrade/
```

### 2. Set your inventory

Edit `inventory/hosts.ini`. Primary devices are upgraded before secondary:

```ini
[bigip_primary]
172.16.1.245

[bigip_secondary]
# 10.1.1.5

[bigip:children]
bigip_primary
bigip_secondary
```

### 3. Store credentials in Ansible Vault

Copy the example file and populate with real credentials:

```bash
cp group_vars/bigip/vault.yml.example group_vars/bigip/vault.yml
```

Edit `group_vars/bigip/vault.yml` and set your credentials:

```yaml
vault_bigip_username: "admin"
vault_bigip_password: "your-password-here"
```

Then encrypt it:

```bash
ansible-vault encrypt group_vars/bigip/vault.yml
```

> **Important:** `vault.yml` is in `.gitignore` and will never be committed. Only `vault.yml.example` (with placeholder values) is tracked in git.

### 4. Run

```bash
ansible-playbook upgrade_bigip.yml --ask-vault-pass
ansible-playbook upgrade_bigip.yml --vault-password-file ~/.vault_pass
```

---

## Tag-based workflows

Each phase of the upgrade has a tag. Use tags to run only the phases you need.

| Tag | Phase | Description |
|---|---|---|
| `preflight` | Pre-flight | ISO/sig file checks, hostname gather, version check, volume selection |
| `backup` | Backup | Create UCS on device and fetch to control node |
| `upload` | Upload | Upload ISO and sig file to BIG-IP `/shared/images/` |
| `install` | Install | Install image to target volume — **no reboot** |
| `reboot` | Reboot | Reboot BIG-IP into target volume |
| `verify` | Verify | Wait for device to be ready, assert expected version is active |

> **Note:** The provider task is tagged `always` and runs regardless of which tags are specified.

---

## Common use cases

### Full upgrade in one shot

```bash
ansible-playbook upgrade_bigip.yml
```

### Upload and install during business hours, reboot in maintenance window

This is the most common real-world pattern. The image is staged to the device in advance so the maintenance window only requires a reboot.

**Step 1 — During business hours:**
```bash
ansible-playbook upgrade_bigip.yml --tags preflight,backup,upload,install
```

**Step 2 — During maintenance window:**
```bash
ansible-playbook upgrade_bigip.yml --tags preflight,reboot,verify
```

### Backup only

```bash
ansible-playbook upgrade_bigip.yml --tags preflight,backup
```

UCS files are saved to `./ucs_backups/<hostname>/` on the control node.

### Pre-flight checks only

```bash
ansible-playbook upgrade_bigip.yml --tags preflight
```

### Upload only

```bash
ansible-playbook upgrade_bigip.yml --tags preflight,upload
```

### Skip backup during testing

```bash
ansible-playbook upgrade_bigip.yml --skip-tags backup
```

### Verbose debug output

```bash
ansible-playbook upgrade_bigip.yml -e debug_mode=true
```

---

## Upgrade workflow detail

For each host (one at a time due to `serial: 1`):

1. **Preflight** — verifies ISO and sig file exist on control node, gathers BIG-IP hostname via `bigip_device_info`
2. **Backup** — creates timestamped UCS on device via `bigip_ucs_fetch`, fetches to `./ucs_backups/<hostname>/`
3. **Version check** — skips host with `meta: end_host` if already running `expected_version`
4. **Volume selection** — selects first inactive volume; falls back to `bigip_fallback_volume`
5. **Upload** — uploads ISO via `bigip_software_image`; uploads `.384.sig` via iControl REST file transfer endpoint
6. **Install** — installs image to target volume via `bigip_software_install` with `state: installed` (no reboot)
7. **Reboot** — issues `reboot volume <target>` via `bigip_command`
8. **Verify** — waits for device to be ready via `bigip_wait`, then asserts expected version is active via `bigip_device_info`

---

## HA pairs

The playbook uses `serial: 1` and `max_fail_percentage: 0`:

- Devices upgrade one at a time
- Any failure stops the run before touching remaining devices
- For HA active/standby — upgrade standby first, verify, then failover and upgrade the other

Structure your inventory to control upgrade order:

```ini
[bigip_primary]   # upgraded first
172.16.1.245

[bigip_secondary] # upgraded only after primary succeeds
# 10.1.1.5
```

---

## Defaults you can override

Defined in `roles/bigip_upgrade/defaults/main.yml`. Override via `group_vars`, `host_vars`, or `--extra-vars`:

| Variable | Default | Description |
|---|---|---|
| `bigip_validate_certs` | `false` | Set to `true` in production with valid certs |
| `debug_mode` | `false` | Set to `true` for verbose volume/version output |
| `bigip_fallback_volume` | `HD1.2` | Used when no inactive volume is detected |
| `bigip_reboot_delay` | `120` | Seconds before `bigip_wait` starts polling after reboot |
| `bigip_reboot_timeout` | `2400` | Max seconds to wait for device to return after reboot |
| `ucs_backup_dest` | `./ucs_backups` | Local directory for UCS backups |
| `ucs_async_timeout` | `600` | Seconds to wait for async UCS create on device (150-1800) |

---

## Troubleshooting

**ISO or sig file not found**
```
fatal: ISO not found at 'BIGIP-21.1.0-0.0.38.iso'
```
Copy both files to the control node. Filenames must match `vars/bigip_upgrade_vars.yml` exactly.

**Version already active — host skipped**
```
TASK [Skip upgrade — target version already active]
```
Expected behavior on re-runs. Device is already running `expected_version`. No action taken.

**Post-upgrade assertion fails**
```
fatal: Upgrade verification FAILED ... Expected active version 21.1.0 not found.
```
Check `tmsh show sys software volume`. Do not proceed to remaining devices until resolved manually.

**Vault decryption error**
```bash
ansible-vault view group_vars/bigip/vault.yml
```

**Check device software volumes manually**
```bash
tmsh show sys software volume
curl -sk -u admin:<password> https://<host>/mgmt/tm/sys/software/volume | python3 -m json.tool
```

---

## Execution environment (ansible-navigator)

`ansible-navigator.yml` references a placeholder registry image. Replace it with your own internally-built EE before using navigator in production or air-gapped environments.

```yaml
# execution-environment.yml
version: 1
dependencies:
  galaxy: requirements.yml      # includes f5networks.f5_modules
  python: requirements.txt
  system: bindep.txt
```

```bash
pip install ansible-builder
ansible-builder build -t your-registry.example.com/f5-ee:1.0.0 --container-runtime docker
docker push your-registry.example.com/f5-ee:1.0.0
```

---

## Useful references

- [F5 Ansible collection docs](https://clouddocs.f5.com/products/orchestration/ansible/devel/)
- [f5networks.f5_modules module index](https://docs.ansible.com/projects/ansible/latest/collections/f5networks/f5_modules/index.html)
- [BIG-IP software image verification (K24341140)](https://my.f5.com/manage/s/article/K24341140)
- [BIG-IP upgrade guide (K84554955)](https://support.f5.com/csp/article/K84554955)
- [Ansible Vault docs](https://docs.ansible.com/ansible/latest/vault_guide/index.html)
- [ansible-builder docs](https://ansible-builder.readthedocs.io/)

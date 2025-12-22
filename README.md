# Ansible BIG-IP Upgrade Automation

An automated and modular approach to upgrading F5 BIG-IP devices using Ansible.

---

## 📖 Overview

This Ansible playbook provides a convenient and automated way to manage software upgrades on F5 BIG-IP systems. It dynamically identifies an available (inactive) software volume, uploads a specified ISO image, installs it, and then reboots the BIG-IP devices. It handles multiple target BIG-IP devices and ensures a reliable, repeatable deployment process.

---

## 📂 Project Structure

```bash
f5_upgrade/
├── ansible.cfg
├── inventory
│   └── hosts.ini
├── upgrade_bigip.yml
└── roles
    └── bigip_upgrade
        ├── handlers
        │   └── main.yml
        ├── tasks
        │   └── main.yml
        └── vars
            └── main.yml
```

---

## ⚙️ Requirements

- **Ansible (2.9 or later recommended)** installed on your Ansible control node.
- The Ansible collection for F5 BIG-IP:
  ```bash
  ansible-galaxy collection install f5networks.f5_modules
  ```
- The BIG-IP ISO image for upgrade must be on the Ansible Control Node at:
  ```bash
  /home/ubuntu/f5_upgrade/BIGIP-17.5.0-0.0.15.iso
  ```

---

## 🔧 Variables and Customizations

Define these variables within `roles/bigip_upgrade/vars/main.yml`:

```yaml
iso_image_name: "BIGIP-17.5.0-0.0.15.iso"
software_volume_prefix: "HD1."
```

Adjust these variables based on your environment if necessary.

---

## 📄 File Descriptions

| File/Folder                          | Description                                          |
|--------------------------------------|------------------------------------------------------|
| **ansible.cfg**                      | Ansible configuration settings (inventory, options). |
| **inventory/hosts.ini**              | Inventory file listing the target BIG-IP hosts.      |
| **upgrade_bigip.yml**                | Main playbook that calls roles on target hosts.      |
| **roles/bigip_upgrade/**             | Contains the tasks, handlers, and variables.         |
| **handlers/main.yml**                | Reboots BIG-IP devices (handled through Ansible handlers).  |
| **tasks/main.yml**                   | Tasks for selecting volumes, ISO uploads, and installation. |
| **vars/main.yml**                    | Centralized location for project-wide variable definitions. |

---

## 🚀 How to Run the Ansible Automation

1\. Clone or download this repo:

```bash
git clone <REPO_URL>
cd ~/f5_upgrade
```

2\. Install the F5 BIG-IP Ansible collection (if needed):

```bash
ansible-galaxy collection install f5networks.f5_modules
```

3\. Ensure ISO is in the expected location on Ansible control node:

```bash
/home/ubuntu/f5_upgrade/BIGIP-17.5.0-0.0.15.iso
```

4\. Run the Ansible Playbook:

```bash
ansible-playbook upgrade_bigip.yml
```
You'll be prompted interactively to provide BIG-IP login credentials securely.

---

## 🚩 Ansible Inventory Setup

File: `inventory/hosts.ini`

Modify this file to include or exclude your BIG-IP devices:

```ini
[bigip]
10.1.1.4
10.1.1.5
```

---

## 🛠️ Tasks Workflow

The tasks executed by the playbook in sequence:

- ✅ Define the BIG-IP API provider connection.
- ✅ Retrieve existing software volumes information.
- ✅ Dynamically select an inactive and available volume for upgrade.
- ✅ Upload BIG-IP ISO upgrade image.
- ✅ Install software to the identified inactive volume.
- ✅ Trigger a BIG-IP system reboot (handled by Ansible handlers).
- ✅ Wait for the device to come back up and verify correct status.

---

## ▶️ Executing the Playbook

Execute from within your playbook directory:

```bash
ansible-playbook upgrade_bigip.yml
```

---

## 🎯 Common Troubleshooting Issues

### Issue: ISO File Not Found
Ensure that your ISO file exists on the Ansible Control Node at the exact path provided. Verify with:
```bash
ls -l /home/ubuntu/f5_upgrade/BIGIP-17.5.0-0.0.15.iso
```

Copy command (from your local workstation to Ansible node):

```bash
scp BIGIP-17.5.0-0.0.15.iso ubuntu@<ansible_host_ip>:/home/ubuntu/f5_upgrade/
```

---

## 💡 Benefits of this Automation

- ✅ Reliable and repeatable BIG-IP upgrade process.
- ✅ Automatically selects unused software volumes dynamically.
- ✅ Clean, modular, and easy-to-debug Ansible structure.
- ✅ Time-saving compared with manual upgrade procedures.

---

## ⚠️ Important Considerations

- **Test before production** — Always test your automation against a non-production system first.
- **Backups recommended** — Before conducting an upgrade, make sure you have UCS/SCF backups:

```bash
tmsh save sys ucs backup-$(date +%F).ucs
```

- **Handling Upgrade Issues** — In case of issues, use debug outputs and TMSH CLI to diagnose:

```bash
tmsh show sys software volume
```

---

## 🚀 What's Next

- Integrate Ansible Vault for secure credential handling.
- Add dynamic inventory sources for scalability.
- Include automated BIG-IP health checks before and after upgrades.
- Combine this pipeline in a CI/CD workflow for infrastructure automation.

---

## 📚 Useful Resources  

- [Ansible F5 Modules Documentation](https://docs.ansible.com/ansible/latest/collections/f5networks/f5_modules/)
- [F5 BIG-IP Upgrade Official Documentation](https://support.f5.com/csp/article/K84554955)
- [F5 Networks GitHub for Ansible Support](https://github.com/F5Networks/f5-ansible)

---

## ✨ Contributing

Pull requests and bug reports are welcome. For major changes, please discuss first by opening an issue.

---

## 📄 License

This project is open-sourced under the [MIT License](LICENSE).

---

## 📞 Contact

_For help, suggestions, or project feedback, please contact: [Your Contact Information]_

Thank you for contributing to and using this BIG-IP upgrade automation solution! 🚀

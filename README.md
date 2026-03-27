# ansible-nxos-iac

Ansible Infrastructure as Code framework for managing Cisco NX-OS datacenter devices.
Designed to enforce configuration consistency across all device types using roles, Jinja2 templates, and a phased rollout model.

---

## Device Types

| Group | Role in Fabric | Topology |
|---|---|---|
| `distro` | Distribution/aggregation switch | Legacy STP |
| `core` | L3 gateway, VRRPv3 | Legacy + VXLAN handoff |
| `spine` | BGP route reflector, underlay backbone | VXLAN fabric |
| `accessleaf` | Host-facing VTEP | VXLAN fabric |
| `gwleaf` | VXLAN stitching (fabric ↔ legacy) | VXLAN fabric |
| `borderleaf` | Fabric edge, external connectivity (L3VNI) | VXLAN fabric |

---

## Directory Structure

```
.
├── ansible.cfg                     # Ansible configuration
├── requirements.yml                # Required collections (cisco.nxos, netcommon)
├── inventory/
│   ├── hosts                       # Static inventory (fallback / testing)
│   ├── netbox_inventory.py         # Dynamic inventory via NetBox API
│   └── group_vars/
│       ├── all.yml                 # Connection settings for all devices
│       ├── nxos.yml                # Shared NX-OS vars (NTP, DNS, syslog)
│       ├── distro.yml              # distro group vars
│       ├── core.yml                # core group vars
│       ├── spine.yml               # spine group vars
│       ├── accessleaf.yml          # accessleaf group vars
│       ├── gwleaf.yml              # gwleaf group vars
│       ├── borderleaf.yml          # borderleaf group vars
│       └── lab.yml                # lab/sandbox overrides
├── host_vars/
│   ├── distro-sw01.yml             # Per-device variable example (distro)
│   ├── core-sw01.yml               # Per-device variable example (core)
│   ├── spine01.yml                 # Per-device variable example (spine)
│   ├── accessleaf01.yml            # Per-device variable example (accessleaf)
│   ├── gwleaf01.yml                # Per-device variable example (gwleaf)
│   └── borderleaf01.yml            # Per-device variable example (borderleaf)
├── roles/
│   ├── nxos_base/                  # Hostname, NTP, DNS, syslog, AAA, features
│   ├── nxos_interfaces/            # L3 ports, SVIs, loopbacks, port-channels
│   ├── nxos_vlans/                 # VLAN definitions and VNI mappings
│   ├── nxos_vpc/                   # VPC domain (L2 redundancy pairs)
│   ├── nxos_ospf/                  # OSPF underlay IGP
│   ├── nxos_bgp/                   # BGP underlay + EVPN overlay
│   ├── nxos_vrf/                   # VRF definitions with L3VNI
│   ├── nxos_vxlan/                 # NVE interface and VNI memberships
│   └── nxos_pim/                   # PIM sparse-mode for multicast BUM
├── playbooks/
│   ├── site.yml                    # Full config push - all devices
│   ├── lab.yml                    # Lab/sandbox only
│   └── drift_check.yml             # Ansible-based drift detection
├── scripts/
│   ├── generate_configs.py         # Render Jinja2 templates locally (no SSH)
│   └── drift_check.py              # Standalone SSH-based drift detection
└── .github/
    └── workflows/
        └── ci.yml                  # GitHub Actions CI (lint + drift check)
```

Each role follows the standard Ansible structure:
```
roles/<role>/
  defaults/main.yml    # Default variable values (lowest precedence)
  tasks/main.yml       # Ansible module calls
  templates/*.j2       # Jinja2 config templates
  handlers/main.yml    # save config handler
```

---

## Setup

### 1. Install dependencies

```bash
pip install ansible
ansible-galaxy collection install -r requirements.yml
```

### 2. Configure credentials

Create a vault password file (keep this out of git — it is in `.gitignore`):

```bash
echo "your_vault_password" > .vault_pass
chmod 600 .vault_pass
```

Encrypt the device password:

```bash
ansible-vault encrypt_string 'your_device_password' --name 'vault_device_password'
```

Paste the output into `inventory/group_vars/all.yml` under `vault_device_password`.

### 3. Configure device inventory

**Option A — Static inventory (testing/no NetBox):**
Edit `inventory/hosts` with your actual device hostnames and management IPs.

**Option B — Dynamic inventory (NetBox):**
```bash
export NETBOX_URL=https://netbox.example.com
export NETBOX_TOKEN=your_api_token
ansible-playbook -i inventory/netbox_inventory.py playbooks/lab.yml
```

### 4. Add host_vars for each device

Copy an example from `host_vars/` and fill in real values for each device:
- Management IP (`ansible_host`)
- Loopback IPs
- VPC peer IPs
- BGP neighbors
- VLAN/VNI lists

---

## Running Playbooks

```bash
# Full push - all devices
ansible-playbook -i inventory/hosts playbooks/site.yml

# Lab/sandbox only
ansible-playbook -i inventory/hosts playbooks/lab.yml

# Specific device
ansible-playbook -i inventory/hosts playbooks/site.yml --limit accessleaf01

# Specific role only
ansible-playbook -i inventory/hosts playbooks/site.yml --tags nxos_bgp

# Dry run (check mode - no changes applied)
ansible-playbook -i inventory/hosts playbooks/site.yml --check --diff
```

---

## Drift Detection

### Ansible-based (uses Ansible connection):
```bash
ansible-playbook -i inventory/hosts playbooks/drift_check.yml
```

### Standalone Python script:
```bash
export DEVICE_USERNAME=your_username
export DEVICE_PASSWORD=your_password

# Check one device
python3 scripts/drift_check.py --device accessleaf01

# Check all devices
python3 scripts/drift_check.py --all

# Check one role on all devices
python3 scripts/drift_check.py --all --role nxos_bgp --output /tmp/drift.txt
```

Exit code `1` = drift found. Exit code `0` = no drift. Useful for CI pipelines.

---

## Config Preview (no SSH required)

Render what a device's config would look like without connecting to it:

```bash
pip install jinja2 pyyaml

# Preview all roles for a device
python3 scripts/generate_configs.py --device accessleaf01

# Preview a single role
python3 scripts/generate_configs.py --device spine01 --role nxos_bgp

# Write to file
python3 scripts/generate_configs.py --device borderleaf01 --output /tmp/borderleaf01.txt
```

---

## Variable Precedence

Ansible applies variables in this order (later = higher priority):

```
group_vars/all.yml
  → group_vars/nxos.yml
    → group_vars/<role_group>.yml   (distro, core, spine, etc.)
      → group_vars/lab.yml         (lab devices only)
        → host_vars/<device>.yml    (device-specific, highest)
```

Role `defaults/main.yml` provides fallback values when nothing else sets a variable.

---

## Phase Roadmap

| Phase | Description | Status |
|---|---|---|
| 1 | Project skeleton, roles, templates, playbooks | Complete |
| 2 | NetBox dynamic inventory | Complete (needs env vars) |
| 3 | SSH connectivity test to lab devices | Pending |
| 4 | Build out nxos_base tasks, push base config to lab | Pending |
| 5 | Config push for all roles, validation | Pending |
| 6 | Drift detection (drift_check.yml + drift_check.py) | Pending |
| 7 | GitHub Actions CI/CD pipeline | Pending |

---

## Collections Used

| Collection | Purpose |
|---|---|
| `cisco.nxos` >= 4.0.0 | NX-OS device modules |
| `ansible.netcommon` >= 5.0.0 | Network CLI connection plugin |
| `ansible.utils` >= 2.0.0 | IP address filters and utilities |

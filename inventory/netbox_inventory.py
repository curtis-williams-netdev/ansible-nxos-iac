#!/usr/bin/env python3
"""
NetBox Dynamic Inventory for Ansible
-------------------------------------
Queries the NetBox API to build Ansible inventory dynamically.
Devices are grouped by their NetBox device role and filtered to NX-OS only.

Requirements:
  pip install requests

Configuration (environment variables):
  NETBOX_URL   - NetBox base URL (e.g. https://netbox.example.com)
  NETBOX_TOKEN - NetBox API token (store in Ansible Vault or CI secret)

Usage:
  ansible-playbook -i inventory/netbox_inventory.py playbooks/site.yml
  ansible-playbook -i inventory/netbox_inventory.py playbooks/lab.yml

Test:
  python3 inventory/netbox_inventory.py --list
  python3 inventory/netbox_inventory.py --host <hostname>
"""

import argparse
import json
import os
import sys

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NETBOX_URL = os.environ.get("NETBOX_URL", "https://netbox.example.com")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")

# Map NetBox device role slugs to Ansible group names
# Update these slugs to match your NetBox device role configuration
ROLE_GROUP_MAP = {
    "distro":      "distro",
    "core":        "core",
    "spine":       "spine",
    "access-leaf": "accessleaf",
    "gw-leaf":     "gwleaf",
    "border-leaf": "borderleaf",
}

# NX-OS platform slug in NetBox - only devices with this platform are included
NXOS_PLATFORM_SLUG = "cisco-nxos"

# Name pattern used to identify lab/sandbox devices
# Devices whose hostname contains this string are added to the lab group
LAB_NAME_PATTERN = "lab"


# ---------------------------------------------------------------------------
# NetBox API helpers
# ---------------------------------------------------------------------------

def netbox_get(path, params=None):
    """
    Paginate through a NetBox API endpoint and return all results.
    NetBox returns results in pages of 50 by default.
    """
    headers = {
        "Authorization": f"Token {NETBOX_TOKEN}",
        "Accept": "application/json",
    }
    results = []
    url = f"{NETBOX_URL}/api/{path}"

    while url:
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(f"ERROR: Cannot connect to NetBox at {NETBOX_URL}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            print(f"ERROR: NetBox API returned {e.response.status_code}: {e}", file=sys.stderr)
            sys.exit(1)

        data = response.json()
        results.extend(data.get("results", []))

        # Follow pagination - NetBox sets 'next' to None on the last page
        url = data.get("next")
        params = None  # params are already encoded in the 'next' URL

    return results


def get_devices():
    """
    Fetch all active NX-OS devices from NetBox.
    Filters: status=active, platform=cisco-nxos
    """
    return netbox_get("dcim/devices/", params={
        "status": "active",
        "platform": NXOS_PLATFORM_SLUG,
    })


# ---------------------------------------------------------------------------
# Inventory builder
# ---------------------------------------------------------------------------

def build_inventory(devices):
    """
    Build the Ansible inventory structure from a list of NetBox device objects.

    Inventory structure:
      - One group per device role (distro, core, spine, etc.)
      - nxos group contains all role groups as children
      - lab group contains devices matching LAB_NAME_PATTERN
      - _meta hostvars contains per-host variables

    Returns a dict matching Ansible dynamic inventory spec.
    """
    inventory = {
        "_meta": {
            "hostvars": {}
        },
        "all": {
            "children": ["nxos"]
        },
        "nxos": {
            "children": list(ROLE_GROUP_MAP.values())
        },
        "lab": {
            "hosts": []
        }
    }

    # Initialize all role groups
    for group in ROLE_GROUP_MAP.values():
        inventory[group] = {"hosts": []}

    for device in devices:
        hostname = device.get("name")
        if not hostname:
            continue

        # Determine management IP
        primary_ip = device.get("primary_ip")
        mgmt_ip = primary_ip["address"].split("/")[0] if primary_ip else None

        # Map device role to Ansible group
        role = device.get("device_role", {}).get("slug", "")
        group = ROLE_GROUP_MAP.get(role)

        if group:
            inventory[group]["hosts"].append(hostname)

        # Add to lab if hostname matches lab pattern
        if LAB_NAME_PATTERN in hostname.lower():
            inventory["lab"]["hosts"].append(hostname)

        # Build per-host vars
        hostvars = {}
        if mgmt_ip:
            hostvars["ansible_host"] = mgmt_ip

        # Pull site/rack/tenant info from NetBox for context
        site = device.get("site", {})
        if site:
            hostvars["netbox_site"] = site.get("name")

        rack = device.get("rack", {})
        if rack:
            hostvars["netbox_rack"] = rack.get("name")

        inventory["_meta"]["hostvars"][hostname] = hostvars

    return inventory


# ---------------------------------------------------------------------------
# Host vars (--host mode)
# ---------------------------------------------------------------------------

def get_host_vars(hostname, devices):
    """
    Return variables for a single host. Called when Ansible passes --host.
    Most vars come from _meta in --list, so this just returns an empty dict
    for hosts already covered there.
    """
    for device in devices:
        if device.get("name") == hostname:
            primary_ip = device.get("primary_ip")
            mgmt_ip = primary_ip["address"].split("/")[0] if primary_ip else None
            return {"ansible_host": mgmt_ip} if mgmt_ip else {}
    return {}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="NetBox dynamic inventory for Ansible")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Output all groups and hosts")
    group.add_argument("--host", metavar="HOSTNAME", help="Output variables for a single host")
    return parser.parse_args()


def main():
    if not NETBOX_TOKEN:
        print("ERROR: NETBOX_TOKEN environment variable is not set.", file=sys.stderr)
        print("       Set it with: export NETBOX_TOKEN=your_token_here", file=sys.stderr)
        sys.exit(1)

    args = parse_args()
    devices = get_devices()

    if args.list:
        inventory = build_inventory(devices)
        print(json.dumps(inventory, indent=2))
    elif args.host:
        hostvars = get_host_vars(args.host, devices)
        print(json.dumps(hostvars, indent=2))


if __name__ == "__main__":
    main()

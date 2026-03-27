#!/usr/bin/env python3
"""
drift_check.py
--------------
Standalone configuration drift detection script.
Connects to NX-OS devices via SSH, pulls the running config,
renders the expected config from Jinja2 templates, and diffs them.

Can run independently of Ansible - useful for scheduled drift checks
or when you want a quick diff without a full playbook run.

Requirements:
  pip install paramiko jinja2 pyyaml

Usage:
  python3 scripts/drift_check.py --device accessleaf01
  python3 scripts/drift_check.py --device accessleaf01 --role nxos_bgp
  python3 scripts/drift_check.py --all
  python3 scripts/drift_check.py --all --output /tmp/drift_report.txt

Environment variables:
  DEVICE_USERNAME - SSH username
  DEVICE_PASSWORD - SSH password (or use --ask-pass flag)

Exit codes:
  0 - No drift detected
  1 - Drift detected
  2 - Error (connection failed, template error, etc.)
"""

import argparse
import difflib
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: 'pyyaml' not found. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

try:
    import paramiko
except ImportError:
    print("ERROR: 'paramiko' not found. Run: pip install paramiko", file=sys.stderr)
    sys.exit(2)

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError
except ImportError:
    print("ERROR: 'jinja2' not found. Run: pip install jinja2", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
GROUP_VARS_DIR = PROJECT_ROOT / "inventory" / "group_vars"
HOST_VARS_DIR = PROJECT_ROOT / "host_vars"
ROLES_DIR = PROJECT_ROOT / "roles"
INVENTORY_FILE = PROJECT_ROOT / "inventory" / "hosts"

HOSTNAME_TO_GROUP = {
    "distro":     "distro",
    "core":       "core",
    "spine":      "spine",
    "accessleaf": "accessleaf",
    "gwleaf":     "gwleaf",
    "borderleaf": "borderleaf",
}

GROUP_ROLES = {
    "distro":     ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_interfaces"],
    "core":       ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_interfaces"],
    "spine":      ["nxos_base", "nxos_ospf", "nxos_bgp", "nxos_interfaces"],
    "accessleaf": ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_vxlan", "nxos_interfaces"],
    "gwleaf":     ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_vxlan", "nxos_interfaces"],
    "borderleaf": ["nxos_base", "nxos_vlans", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_vxlan", "nxos_interfaces"],
}


# ---------------------------------------------------------------------------
# Variable loading (shared with generate_configs.py)
# ---------------------------------------------------------------------------

def load_yaml(path):
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_vars(device_name):
    vars_context = {}
    for group_file in ["all.yml", "nxos.yml"]:
        vars_context.update(load_yaml(GROUP_VARS_DIR / group_file))

    device_group = None
    for prefix, group in HOSTNAME_TO_GROUP.items():
        if device_name.startswith(prefix):
            device_group = group
            break

    if device_group:
        vars_context.update(load_yaml(GROUP_VARS_DIR / f"{device_group}.yml"))
        for role in GROUP_ROLES.get(device_group, []):
            defaults = load_yaml(ROLES_DIR / role / "defaults" / "main.yml")
            for key, value in defaults.items():
                if key not in vars_context:
                    vars_context[key] = value

    vars_context.update(load_yaml(HOST_VARS_DIR / f"{device_name}.yml"))
    vars_context["inventory_hostname"] = device_name
    return vars_context, device_group


# ---------------------------------------------------------------------------
# SSH - pull running config from device
# ---------------------------------------------------------------------------

def get_running_config(hostname, mgmt_ip, username, password):
    """
    SSH to the device and run 'show running-config'.
    Returns the config as a string.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=mgmt_ip,
            username=username,
            password=password,
            timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command("show running-config")
        output = stdout.read().decode("utf-8")
        client.close()
        return output
    except paramiko.AuthenticationException:
        print(f"ERROR: Authentication failed for {hostname} ({mgmt_ip})", file=sys.stderr)
        return None
    except paramiko.ssh_exception.NoValidConnectionsError:
        print(f"ERROR: Cannot connect to {hostname} ({mgmt_ip})", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: SSH error on {hostname}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_expected_config(device_name, device_group, vars_context, role=None):
    """Render all applicable templates and concatenate into one expected config."""
    roles = [role] if role else GROUP_ROLES.get(device_group, [])
    rendered_parts = []

    for r in roles:
        template_dir = ROLES_DIR / r / "templates"
        if not template_dir.exists():
            continue
        templates = list(template_dir.glob("*.j2"))
        if not templates:
            continue

        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        try:
            rendered = env.get_template(templates[0].name).render(**vars_context)
            rendered_parts.append(rendered)
        except UndefinedError as e:
            print(f"WARNING: Template variable missing for {device_name}/{r}: {e}", file=sys.stderr)

    return "\n".join(rendered_parts)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff_configs(expected, actual, device_name):
    """
    Generate a unified diff between expected (rendered template) and
    actual (running config pulled from device).
    Returns the diff as a string. Empty string means no drift.
    """
    expected_lines = expected.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        expected_lines,
        actual_lines,
        fromfile=f"{device_name} (expected)",
        tofile=f"{device_name} (running)",
    ))

    return "".join(diff)


# ---------------------------------------------------------------------------
# Inventory parsing
# ---------------------------------------------------------------------------

def get_all_devices():
    """
    Parse the static inventory/hosts file to get a list of device names.
    Returns a list of hostnames.
    """
    devices = []
    if not INVENTORY_FILE.exists():
        print(f"ERROR: inventory/hosts not found at {INVENTORY_FILE}", file=sys.stderr)
        sys.exit(2)

    with open(INVENTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            hostname = line.split()[0]
            devices.append(hostname)

    return list(dict.fromkeys(devices))  # deduplicate while preserving order


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="NX-OS config drift detection")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--device", help="Check a single device by hostname")
    target.add_argument("--all", action="store_true", help="Check all devices in inventory/hosts")
    parser.add_argument("--role", default=None, help="Limit check to a single role's template")
    parser.add_argument("--output", default=None, help="Write drift report to this file")
    parser.add_argument("--ask-pass", action="store_true", help="Prompt for device password")
    return parser.parse_args()


def main():
    args = parse_args()

    username = os.environ.get("DEVICE_USERNAME")
    password = os.environ.get("DEVICE_PASSWORD")

    if not username:
        print("ERROR: DEVICE_USERNAME environment variable not set.", file=sys.stderr)
        sys.exit(2)

    if args.ask_pass:
        import getpass
        password = getpass.getpass(f"Password for {username}: ")
    elif not password:
        print("ERROR: DEVICE_PASSWORD not set and --ask-pass not specified.", file=sys.stderr)
        sys.exit(2)

    devices = [args.device] if args.device else get_all_devices()
    drift_found = False
    report_parts = []

    for device_name in devices:
        vars_context, device_group = load_vars(device_name)
        mgmt_ip = vars_context.get("ansible_host")

        if not mgmt_ip:
            print(f"WARNING: No ansible_host defined for {device_name}, skipping.", file=sys.stderr)
            continue

        print(f"Checking {device_name} ({mgmt_ip})...", end=" ", flush=True)

        running_config = get_running_config(device_name, mgmt_ip, username, password)
        if running_config is None:
            print("SKIP (connection failed)")
            continue

        expected_config = render_expected_config(device_name, device_group, vars_context, args.role)
        diff = diff_configs(expected_config, running_config, device_name)

        if diff:
            print("DRIFT DETECTED")
            drift_found = True
            report_parts.append(f"\n{'='*60}\nDRIFT: {device_name}\n{'='*60}\n{diff}")
        else:
            print("OK")

    report = "\n".join(report_parts) if report_parts else "No drift detected across all checked devices."

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"\nDrift report written to {args.output}")
    elif drift_found:
        print("\n" + report)

    sys.exit(1 if drift_found else 0)


if __name__ == "__main__":
    main()

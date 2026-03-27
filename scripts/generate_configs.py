#!/usr/bin/env python3
"""
generate_configs.py
--------------------
Renders Jinja2 templates locally without running a full Ansible playbook.
Useful for previewing what a device config will look like before pushing.

Requirements:
  pip install jinja2 pyyaml

Usage:
  python3 scripts/generate_configs.py --device accessleaf01
  python3 scripts/generate_configs.py --device spine01 --role nxos_bgp
  python3 scripts/generate_configs.py --device accessleaf01 --output /tmp/accessleaf01_vxlan.txt

How it works:
  1. Loads group_vars (all.yml, nxos.yml, role group) for variable context
  2. Loads host_vars/<device>.yml for device-specific overrides
  3. Renders the Jinja2 template for the specified role
  4. Prints the result to stdout or writes to a file
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: 'pyyaml' not found. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError
except ImportError:
    print("ERROR: 'jinja2' not found. Run: pip install jinja2", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Paths (relative to project root)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
GROUP_VARS_DIR = PROJECT_ROOT / "inventory" / "group_vars"
HOST_VARS_DIR = PROJECT_ROOT / "host_vars"
ROLES_DIR = PROJECT_ROOT / "roles"

# Map device hostname prefix to its role group name
# Used to load the correct group_vars file for shared defaults
HOSTNAME_TO_GROUP = {
    "distro":      "distro",
    "core":        "core",
    "spine":       "spine",
    "accessleaf":  "accessleaf",
    "gwleaf":      "gwleaf",
    "borderleaf":  "borderleaf",
}

# Map role group to the roles that apply to it
GROUP_ROLES = {
    "distro":     ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_interfaces"],
    "core":       ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_interfaces"],
    "spine":      ["nxos_base", "nxos_ospf", "nxos_bgp", "nxos_interfaces"],
    "accessleaf": ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_vxlan", "nxos_interfaces", "nxos_pim"],
    "gwleaf":     ["nxos_base", "nxos_vlans", "nxos_vpc", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_vxlan", "nxos_interfaces", "nxos_pim"],
    "borderleaf": ["nxos_base", "nxos_vlans", "nxos_ospf", "nxos_bgp", "nxos_vrf", "nxos_vxlan", "nxos_interfaces", "nxos_pim"],
}


# ---------------------------------------------------------------------------
# Variable loading
# ---------------------------------------------------------------------------

def load_yaml(path):
    """Load a YAML file and return its contents as a dict."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_vars(device_name):
    """
    Build the variable context for a device by merging:
      group_vars/all.yml < group_vars/nxos.yml < group_vars/<role>.yml < host_vars/<device>.yml
    Later files override earlier ones (matches Ansible precedence order).
    """
    vars_context = {}

    # Load group_vars in precedence order (least specific to most specific)
    for group_file in ["all.yml", "nxos.yml"]:
        path = GROUP_VARS_DIR / group_file
        vars_context.update(load_yaml(path))

    # Detect device role group from hostname prefix
    device_group = None
    for prefix, group in HOSTNAME_TO_GROUP.items():
        if device_name.startswith(prefix):
            device_group = group
            break

    if device_group:
        group_vars_path = GROUP_VARS_DIR / f"{device_group}.yml"
        vars_context.update(load_yaml(group_vars_path))

    # Load role defaults for all applicable roles
    if device_group:
        for role in GROUP_ROLES.get(device_group, []):
            defaults_path = ROLES_DIR / role / "defaults" / "main.yml"
            # Only set keys not already defined (defaults have lowest precedence)
            defaults = load_yaml(defaults_path)
            for key, value in defaults.items():
                if key not in vars_context:
                    vars_context[key] = value

    # Load host_vars (highest precedence - overrides everything)
    host_vars_path = HOST_VARS_DIR / f"{device_name}.yml"
    vars_context.update(load_yaml(host_vars_path))

    # Set inventory_hostname so templates can reference it
    vars_context["inventory_hostname"] = device_name

    return vars_context, device_group


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_template(role, vars_context):
    """Render the Jinja2 template for the given role using the provided vars."""
    template_dir = ROLES_DIR / role / "templates"
    if not template_dir.exists():
        print(f"ERROR: No templates directory found for role '{role}'", file=sys.stderr)
        sys.exit(1)

    # Find the template file (assumes one .j2 file per role)
    templates = list(template_dir.glob("*.j2"))
    if not templates:
        print(f"ERROR: No .j2 template found in {template_dir}", file=sys.stderr)
        sys.exit(1)

    template_file = templates[0]

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    template = env.get_template(template_file.name)

    try:
        return template.render(**vars_context)
    except UndefinedError as e:
        print(f"ERROR: Template variable not defined: {e}", file=sys.stderr)
        print("       Add the missing variable to the device's host_vars file.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Render Ansible Jinja2 templates locally for config preview"
    )
    parser.add_argument(
        "--device", required=True,
        help="Device hostname (must match a file in host_vars/)"
    )
    parser.add_argument(
        "--role", default=None,
        help="Role name to render (e.g. nxos_bgp). If omitted, renders all applicable roles."
    )
    parser.add_argument(
        "--output", default=None,
        help="Write rendered config to this file instead of stdout"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    device = args.device

    vars_context, device_group = load_vars(device)

    if not device_group:
        print(f"WARNING: Could not determine role group for '{device}'.", file=sys.stderr)
        print(f"         Hostname must start with one of: {list(HOSTNAME_TO_GROUP.keys())}", file=sys.stderr)

    # Determine which roles to render
    if args.role:
        roles_to_render = [args.role]
    elif device_group:
        roles_to_render = GROUP_ROLES.get(device_group, [])
    else:
        print("ERROR: Specify --role or use a hostname that maps to a known device group.", file=sys.stderr)
        sys.exit(1)

    rendered_parts = []
    for role in roles_to_render:
        template_dir = ROLES_DIR / role / "templates"
        if not template_dir.exists():
            continue
        rendered = render_template(role, vars_context)
        rendered_parts.append(f"! --- {role} ---\n{rendered}")

    output = "\n".join(rendered_parts)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Config written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()

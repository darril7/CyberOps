"""
netbox_import.py
================
Reads the Excel sheet exported from form.html and imports each device
into NetBox. All mapping has already been done by the form — this script
just reads the columns and writes them straight to the NetBox API.

Usage:
    python netbox_import.py --file device_onboarding_2026-01-01.xlsx
    python netbox_import.py --file device_onboarding_2026-01-01.xlsx --dry-run
    python netbox_import.py --file device_onboarding_2026-01-01.xlsx --only-review

Requirements:
    pip install pynetbox pandas openpyxl

Config:
    export NETBOX_URL=http://your-netbox-instance
    export NETBOX_TOKEN=your-api-token
"""

import os
import sys
import argparse
import pandas as pd
import pynetbox

NETBOX_URL   = os.getenv("NETBOX_URL",   "http://your-netbox-instance")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN", "your-api-token")
EXCEL_SHEET  = "Device Onboarding"

# Fields that accept multiple values (comma-separated in Excel -> list in NetBox API)
MULTISELECT_FIELDS = ["sigma_product", "sigma_log_categories"]

# If you rename a custom field in NetBox, map old -> new name here.
# Example: {"sigma_product": "soc_sigma_product"}
FIELD_NAMES = {}


def safe_get(queryset, **kwargs):
    try:
        return queryset.get(**kwargs)
    except Exception:
        return None


def clean(value) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


def to_multiselect(value: str) -> list:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def to_bool(value: str) -> bool:
    return str(value).strip().lower() in ("yes", "true", "1")


def build_custom_fields(row: dict, multiselect_fields: list, field_names: dict) -> dict:
    """
    Direct read from Excel columns to NetBox custom field payload.
    No mapping logic — all values are already resolved by the form.
    field_names dict allows renaming a NetBox field without touching this function.
    """
    def fn(logical):
        return field_names.get(logical, logical)

    cf = {}

    # Group 1 — Device Classification
    cf[fn("device_function")]   = clean(row.get("device_function")) or None
    cf[fn("internet_facing")]   = to_bool(row.get("internet_facing", "No"))
    specific = clean(row.get("specific_product"))
    if specific:
        cf[fn("specific_product")] = specific

    # Group 2 — OS Hardening
    cf[fn("cis_os_benchmark")] = clean(row.get("cis_os_benchmark")) or None
    cf[fn("cis_os_profile")]   = clean(row.get("cis_os_profile")) or "L1"
    cf[fn("cis_os_status")]    = clean(row.get("cis_os_status")) or "NOT-STARTED"

    # Group 3 — App Hardening
    cf[fn("cis_app_benchmark")]         = clean(row.get("cis_app_benchmark")) or None
    cf[fn("cis_app_status")]            = clean(row.get("cis_app_status")) or "NOT-STARTED"
    cf[fn("hardening_exception_notes")] = clean(row.get("hardening_exception_notes"))

    # Group 4 — Log Collection
    for field in multiselect_fields:
        cf[fn(field)] = to_multiselect(clean(row.get(field, "")))

    cf[fn("log_collection_status")] = clean(row.get("log_collection_status")) or "NOT-CONFIGURED"
    cf[fn("log_collector")]         = clean(row.get("log_collector")) or "NONE"
    cf[fn("business_app_log_path")] = clean(row.get("business_app_log_path"))

    # Strip empty strings — NetBox prefers None for unset optional fields
    return {k: v for k, v in cf.items() if v != ""}


def get_role(nb, asset_type: str):
    slug_map = {"server": "server", "workstation": "workstation", "appliance": "appliance"}
    slug = slug_map.get(asset_type.lower(), "server")
    return safe_get(nb.dcim.device_roles, slug=slug)


def get_device_type(nb):
    dt = safe_get(nb.dcim.device_types, model="Generic")
    if not dt:
        all_types = list(nb.dcim.device_types.all())
        return all_types[0] if all_types else None
    return dt


def import_device(nb, row: dict, dry_run: bool = False) -> tuple:
    hostname = clean(row.get("hostname"))
    if not hostname:
        return "SKIP", "no hostname"

    if "REVIEW" in clean(row.get("import_status", "")).upper():
        return "REVIEW", "marked for manual review"

    asset_type = clean(row.get("asset_type", "Server"))
    site_slug  = clean(row.get("site", ""))

    cf = build_custom_fields(
        row,
        multiselect_fields=MULTISELECT_FIELDS,
        field_names=FIELD_NAMES,
    )

    if dry_run:
        print(f"         device_function:   {cf.get('device_function', '—')}")
        print(f"         cis_os_benchmark:  {cf.get('cis_os_benchmark', '—')}")
        print(f"         cis_os_profile:    {cf.get('cis_os_profile', '—')}")
        print(f"         cis_app_benchmark: {cf.get('cis_app_benchmark', '—')}")
        print(f"         sigma_product:     {cf.get('sigma_product', '—')}")
        mac = clean(row.get("mac_address"))
        if mac:
            print(f"         mac_address:       {mac}  → Management interface")
        return "DRY-RUN", "ok"

    role = get_role(nb, asset_type)
    if not role:
        return "ERROR", f"role for '{asset_type}' not found in NetBox"

    site        = safe_get(nb.dcim.sites, slug=site_slug) if site_slug else None
    ip_address  = clean(row.get("ip_address"))
    mac_address = clean(row.get("mac_address"))
    existing    = safe_get(nb.dcim.devices, name=hostname)

    if existing:
        existing.custom_fields = cf
        try:
            existing.save()
            # Update MAC on existing Management interface if provided
            if mac_address:
                _upsert_mac(nb, existing.id, mac_address)
            return "UPDATED", ""
        except Exception as e:
            return "ERROR", str(e)

    device_type = get_device_type(nb)
    if not device_type:
        return "ERROR", "no device types in NetBox — create one first"

    payload = {
        "name":          hostname,
        "device_type":   device_type.id,
        "role":          role.id,
        "status":        "active",
        "custom_fields": cf,
    }
    if site:
        payload["site"] = site.id

    try:
        new_device = nb.dcim.devices.create(payload)

        # Create Management interface + assign MAC
        if mac_address:
            try:
                _upsert_mac(nb, new_device.id, mac_address)
            except Exception as e:
                print(f"\n         [WARN] MAC assignment failed: {e}", end="")

        # Assign IP and link to Management interface if possible
        if ip_address:
            try:
                addr    = ip_address if "/" in ip_address else f"{ip_address}/32"
                # Try to find the Management interface we just created
                mgmt_if = safe_get(nb.dcim.interfaces, device_id=new_device.id, name="Management")
                ip_payload = {
                    "address": addr,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id":   mgmt_if.id,
                } if mgmt_if else {
                    "address": addr,
                    "assigned_object_type": "dcim.device",
                    "assigned_object_id":   new_device.id,
                }
                ip_obj = nb.ipam.ip_addresses.create(ip_payload)
                new_device.primary_ip4 = ip_obj.id
                new_device.save()
            except Exception as e:
                print(f"\n         [WARN] IP assignment failed: {e}", end="")

        return "CREATED", ""
    except Exception as e:
        return "ERROR", str(e)


def _upsert_mac(nb, device_id: int, mac_address: str):
    """
    Create or update a 'Management' interface on the device with the given MAC.
    NetBox stores MAC addresses on interfaces, not directly on devices.
    """
    # Normalize MAC format — accept AA:BB:CC:DD:EE:FF or AA-BB-CC-DD-EE-FF
    mac = mac_address.upper().replace("-", ":").strip()

    existing_if = safe_get(nb.dcim.interfaces, device_id=device_id, name="Management")
    if existing_if:
        existing_if.mac_address = mac
        existing_if.save()
    else:
        nb.dcim.interfaces.create({
            "device":       device_id,
            "name":         "Management",
            "type":         "1000base-t",   # gigabit ethernet — adjust if needed
            "mac_address":  mac,
            "description":  "Primary management interface",
        })


def main():
    parser = argparse.ArgumentParser(description="SOC NetBox Device Import")
    parser.add_argument("--file",        required=True,       help="Excel file exported from onboarding form")
    parser.add_argument("--dry-run",     action="store_true", help="Preview without writing to NetBox")
    parser.add_argument("--only-review", action="store_true", help="Show only REVIEW NEEDED rows")
    parser.add_argument("--sheet",       default=EXCEL_SHEET, help=f"Excel sheet name (default: '{EXCEL_SHEET}')")
    args = parser.parse_args()

    print("=" * 60)
    print("  SOC Device Onboarding — NetBox Import")
    print(f"  File:    {args.file}")
    print(f"  Mode:    {'DRY RUN — no changes' if args.dry_run else 'LIVE IMPORT'}")
    print(f"  NetBox:  {NETBOX_URL}")
    print("=" * 60)


    try:
        df = pd.read_excel(args.file, sheet_name=args.sheet, dtype=str).fillna("")
        print(f"  Excel:   {len(df)} device(s)\n")
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    nb = None
    if not args.dry_run:
        nb = pynetbox.api(NETBOX_URL, token=NETBOX_TOKEN)
        nb.http_session.verify = False
        try:
            nb.dcim.sites.count()
            print("  NetBox:  connected\n")
        except Exception as e:
            print(f"[ERROR] Cannot connect to NetBox: {e}")
            sys.exit(1)

    counts = {"CREATED": 0, "UPDATED": 0, "SKIP": 0, "DRY-RUN": 0, "REVIEW": 0, "ERROR": 0}
    review = []
    errors = []

    for idx, row in df.iterrows():
        hostname = clean(row.get("hostname", ""))
        if args.only_review and "REVIEW" not in clean(row.get("import_status", "")).upper():
            continue

        print(f"  [{idx+1:>3}] {hostname:<32}", end="", flush=True)
        status, msg = import_device(nb, row.to_dict(), dry_run=args.dry_run)

        counts[status] = counts.get(status, 0) + 1
        icon = {"CREATED": "✓", "UPDATED": "↻", "SKIP": "—", "DRY-RUN": "○", "REVIEW": "⚠", "ERROR": "✗"}.get(status, "?")
        print(f"{icon}  {status}{'  ' + msg if msg else ''}")

        if status == "REVIEW":
            review.append(hostname)
        if status == "ERROR":
            errors.append((hostname, msg))

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    if args.dry_run:
        print(f"  DRY RUN  — {counts.get('DRY-RUN', 0)} previewed, no changes")
    else:
        print(f"  Created:  {counts.get('CREATED', 0)}")
        print(f"  Updated:  {counts.get('UPDATED', 0)}")
        print(f"  Skipped:  {counts.get('SKIP', 0)}")
        print(f"  Errors:   {counts.get('ERROR', 0)}")

    if review:
        print(f"\n  ⚠  REVIEW NEEDED ({len(review)}) — fix device_function in Excel → re-run:")
        for h in review:
            print(f"     — {h}")

    if errors:
        print(f"\n  ✗  ERRORS:")
        for h, e in errors:
            print(f"     — {h}: {e}")

    print("=" * 60)


if __name__ == "__main__":
    main()

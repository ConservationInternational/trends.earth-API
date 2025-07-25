#!/usr/bin/env python3
"""
Generate SRI hashes for Swagger UI CDN resources and optionally update the code
"""

import base64
import hashlib
import os
import re
import sys

import requests


def generate_sri_hash(url):
    """Generate SHA384 SRI hash for a given URL"""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Calculate SHA384 hash
        sha384_hash = hashlib.sha384(response.content).digest()
        # Base64 encode
        sri_hash = base64.b64encode(sha384_hash).decode("ascii")

        return f"sha384-{sri_hash}"
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def update_init_file(css_sri, js_sri, version="4.15.5"):
    """Update the __init__.py file with new SRI hashes"""
    init_file = "gefapi/__init__.py"

    if not os.path.exists(init_file):
        print(f"Error: {init_file} not found")
        return False

    try:
        with open(init_file) as f:
            content = f.read()

        # Update CSS integrity
        css_pattern = r'(href="https://unpkg\.com/swagger-ui-dist@[\d\.]+/swagger-ui\.css"\s+integrity=")([^"]*)"'
        content = re.sub(css_pattern, f'\\1{css_sri}"', content)

        # Update JS integrity
        js_pattern = r'(src="https://unpkg\.com/swagger-ui-dist@[\d\.]+/swagger-ui-bundle\.js"\s+integrity=")([^"]*)"'
        content = re.sub(js_pattern, f'\\1{js_sri}"', content)

        # Write back
        with open(init_file, "w") as f:
            f.write(content)

        print(f"✅ Updated {init_file} with new SRI hashes")
        return True

    except Exception as e:
        print(f"Error updating {init_file}: {e}")
        return False


def main():
    version = "4.15.5"
    css_url = f"https://unpkg.com/swagger-ui-dist@{version}/swagger-ui.css"
    js_url = f"https://unpkg.com/swagger-ui-dist@{version}/swagger-ui-bundle.js"

    print(f"Generating SRI hashes for Swagger UI {version}...")
    print("=" * 50)

    print("Fetching CSS file...")
    css_sri = generate_sri_hash(css_url)
    if not css_sri:
        sys.exit(1)

    print("Fetching JS file...")
    js_sri = generate_sri_hash(js_url)
    if not js_sri:
        sys.exit(1)

    print("\n📋 Generated SRI Hashes:")
    print(f"CSS: {css_sri}")
    print(f"JS:  {js_sri}")

    # Ask to update file
    if len(sys.argv) > 1 and sys.argv[1] == "--update":
        print("\n🔧 Updating gefapi/__init__.py...")
        if update_init_file(css_sri, js_sri, version):
            print("✅ File updated successfully!")
        else:
            print("❌ Failed to update file")
            sys.exit(1)
    else:
        print("\n💡 To automatically update the code, run:")
        print(f"   python3 {sys.argv[0]} --update")


if __name__ == "__main__":
    main()

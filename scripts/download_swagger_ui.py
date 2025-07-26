#!/usr/bin/env python3
"""
Download Swagger UI assets from unpkg.com and save them locally.
This script downloads the CSS and JS files for Swagger UI and stores them
in the gefapi/static/swagger-ui/ directory.
"""

import argparse
import base64
from datetime import datetime
import hashlib
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


def calculate_sri_hash(content: bytes) -> str:
    """Calculate SHA384 hash for SRI (Subresource Integrity)"""
    hash_obj = hashlib.sha384(content)
    return f"sha384-{base64.b64encode(hash_obj.digest()).decode()}"


def download_file(url: str, local_path: Path) -> tuple[bool, str]:
    """
    Download a file from URL and save it locally.
    Returns (success, sri_hash)
    """
    # Validate URL scheme for security
    if not url.startswith("https://unpkg.com/"):
        print(f"✗ Invalid URL: {url} - Only unpkg.com HTTPS URLs are allowed")
        return False, ""

    try:
        print(f"Downloading {url}...")

        # Create request with user agent to avoid blocks
        req = Request(  # noqa: S310
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Swagger UI downloader)"},
        )

        with urlopen(req) as response:  # noqa: S310
            content = response.read()

        # Ensure parent directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        with open(local_path, "wb") as f:
            f.write(content)

        # Calculate SRI hash
        sri_hash = calculate_sri_hash(content)

        print(f"✓ Downloaded {local_path} ({len(content)} bytes)")
        print(f"  SRI hash: {sri_hash}")

        return True, sri_hash

    except URLError as e:
        print(f"✗ Failed to download {url}: {e}")
        return False, ""
    except Exception as e:
        print(f"✗ Error downloading {url}: {e}")
        return False, ""


def main():
    """Main function to download Swagger UI assets"""

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Download Swagger UI assets and host them locally"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download without confirmation, overwriting existing files",
    )
    parser.add_argument(
        "--version",
        default="4.15.5",
        help="Swagger UI version to download (default: 4.15.5)",
    )
    args = parser.parse_args()

    # Configuration - Update this when upgrading Swagger UI
    swagger_version = args.version

    # Get project root directory
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    static_dir = project_root / "gefapi" / "static" / "swagger-ui"

    print(f"Downloading Swagger UI v{swagger_version} assets...")
    print(f"Target directory: {static_dir}")

    # Check if files already exist and prompt for confirmation (unless --force is used)
    css_file = static_dir / "swagger-ui.css"
    js_file = static_dir / "swagger-ui-bundle.js"

    if (css_file.exists() or js_file.exists()) and not args.force:
        print("\nExisting Swagger UI files found.")
        response = input(
            "Download new version? This will overwrite existing files. (y/N): "
        )
        if response.lower() != "y":
            print("Download cancelled.")
            return
    elif args.force and (css_file.exists() or js_file.exists()):
        print("✅ Force flag set - overwriting existing files...")

    # URLs to download
    files_to_download = [
        {
            "url": f"https://unpkg.com/swagger-ui-dist@{swagger_version}/swagger-ui.css",
            "local_path": static_dir / "swagger-ui.css",
            "name": "CSS",
        },
        {
            "url": f"https://unpkg.com/swagger-ui-dist@{swagger_version}/swagger-ui-bundle.js",
            "local_path": static_dir / "swagger-ui-bundle.js",
            "name": "JavaScript Bundle",
        },
    ]  # Download files
    success_count = 0
    sri_hashes = {}

    for file_info in files_to_download:
        success, sri_hash = download_file(file_info["url"], file_info["local_path"])
        if success:
            success_count += 1
            sri_hashes[file_info["name"]] = sri_hash

    if success_count == len(files_to_download):
        print(f"\n✓ Successfully downloaded all {success_count} files!")

        # Create a summary file with SRI hashes
        summary_file = static_dir / "download_info.txt"
        with open(summary_file, "w") as f:
            f.write(f"Swagger UI v{swagger_version} - Downloaded assets\n")
            f.write(f"Download date: {datetime.now().isoformat()}\n\n")
            f.write("SRI Hashes:\n")
            for name, sri_hash in sri_hashes.items():
                f.write(f"{name}: {sri_hash}\n")

        print(f"✓ Created summary file: {summary_file}")

        print("\nNext steps:")
        print("1. Update gefapi/__init__.py to use local files")
        print("2. Remove unpkg.com references from CSP headers")
        print("3. Test the API documentation at /api/docs/")

    else:
        print(f"\n✗ Failed to download {len(files_to_download) - success_count} files")
        sys.exit(1)


if __name__ == "__main__":
    main()

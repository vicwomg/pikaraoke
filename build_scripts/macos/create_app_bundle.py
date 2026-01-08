"""
macOS .app bundle creation script for PiKaraoke.

This script creates a proper macOS .app bundle from the PyInstaller output
and packages it into a DMG file for distribution.

Usage:
    python create_app_bundle.py <project_root>

Args:
    project_root: Path to the project root directory
"""

import shutil
import subprocess
import sys
from pathlib import Path


def create_app_bundle(project_root: Path):
    """
    Create macOS .app bundle from PyInstaller output.

    Args:
        project_root: Path to the project root directory

    Raises:
        RuntimeError: If bundle creation fails
    """
    print("Creating macOS .app bundle...")

    dist_dir = project_root / "dist"
    pyinstaller_output = dist_dir / "pikaraoke"
    app_name = "PiKaraoke.app"
    app_path = dist_dir / app_name

    if not pyinstaller_output.exists():
        raise RuntimeError(f"PyInstaller output not found at {pyinstaller_output}")

    if app_path.exists():
        print(f"Removing existing .app bundle at {app_path}")
        shutil.rmtree(app_path)

    # Create .app bundle structure
    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    print(f"Created .app bundle structure at {app_path}")

    # Move PyInstaller output to MacOS directory
    print("Moving PyInstaller output to .app bundle...")
    for item in pyinstaller_output.iterdir():
        dest = macos_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # Copy icon file
    icon_source = project_root / "pikaraoke" / "static" / "icons" / "logo.icns"
    icon_dest = resources_dir / "pikaraoke.icns"

    if icon_source.exists():
        shutil.copy2(icon_source, icon_dest)
        print(f"Copied icon to {icon_dest}")
    else:
        print(f"Warning: Icon not found at {icon_source}")

    # Create Info.plist
    create_info_plist(contents_dir)

    # Code sign the .app bundle (ad-hoc)
    print("Code signing .app bundle...")
    try:
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        print("Code signing completed")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Code signing failed: {e}")
        print(e.stderr)

    # Create ZIP for distribution
    print("Creating ZIP archive...")
    zip_path = dist_dir / "pikaraoke_mac.zip"
    if zip_path.exists():
        zip_path.unlink()

    try:
        subprocess.run(
            ["ditto", "-c", "-k", "--keepParent", str(app_path), str(zip_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Created ZIP at {zip_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error creating ZIP: {e}")
        print(e.stderr)
        raise RuntimeError("Failed to create ZIP") from e

    # Try to create DMG if create-dmg is available
    try:
        create_dmg(app_path, dist_dir)
    except Exception as e:
        print(f"Note: Could not create DMG (this is optional): {e}")
        print("The .zip file can be used for distribution.")

    # Clean up intermediate directories
    cleanup_intermediate(pyinstaller_output)

    print("macOS packaging completed successfully")


def create_info_plist(contents_dir: Path):
    """
    Create Info.plist file for the .app bundle.

    Args:
        contents_dir: Path to the Contents directory
    """
    plist_path = contents_dir / "Info.plist"

    plist_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>pikaraoke</string>
    <key>CFBundleIconFile</key>
    <string>pikaraoke.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.pikaraoke.app</string>
    <key>CFBundleName</key>
    <string>PiKaraoke</string>
    <key>CFBundleDisplayName</key>
    <string>PiKaraoke</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
"""

    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(plist_content)

    print(f"Created Info.plist at {plist_path}")


def cleanup_intermediate(pyinstaller_output: Path):
    """
    Clean up intermediate PyInstaller output directory.

    Args:
        pyinstaller_output: Path to the PyInstaller output directory
    """
    print("Cleaning up intermediate directories...")

    if pyinstaller_output.exists():
        try:
            shutil.rmtree(pyinstaller_output)
            print(f"Removed intermediate directory: {pyinstaller_output}")
        except Exception as e:
            print(f"Warning: Could not remove {pyinstaller_output}: {e}")

    print("Cleanup completed")


def create_dmg(app_path: Path, dist_dir: Path):
    """
    Create DMG file from .app bundle using create-dmg if available.

    Args:
        app_path: Path to the .app bundle
        dist_dir: Path to the dist directory

    Raises:
        RuntimeError: If DMG creation fails
    """
    dmg_path = dist_dir / "pikaraoke_mac.dmg"

    if dmg_path.exists():
        dmg_path.unlink()

    print("Attempting to create DMG...")

    # Try using create-dmg if available
    try:
        subprocess.run(["which", "create-dmg"], check=True, capture_output=True, text=True)

        # create-dmg is available
        subprocess.run(
            [
                "create-dmg",
                "--volname",
                "PiKaraoke",
                "--window-pos",
                "200",
                "120",
                "--window-size",
                "600",
                "400",
                "--icon-size",
                "100",
                "--app-drop-link",
                "450",
                "185",
                str(dmg_path),
                str(app_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Created DMG at {dmg_path}")
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("create-dmg not available, trying hdiutil...")

    # Fallback to hdiutil
    try:
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "PiKaraoke",
                "-srcfolder",
                str(app_path),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Created DMG at {dmg_path} using hdiutil")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not create DMG: {e}")
        print(e.stderr)
        raise


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 2:
        print("Usage: python create_app_bundle.py <project_root>")
        return 1

    project_root = Path(sys.argv[1]).resolve()

    if not project_root.exists():
        print(f"Error: Project root does not exist: {project_root}")
        return 1

    try:
        create_app_bundle(project_root)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

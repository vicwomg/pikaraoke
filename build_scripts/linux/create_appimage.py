"""
Linux AppImage creation script for PiKaraoke.

This script creates an AppImage from the PyInstaller output for easy
distribution on Linux systems.

Usage:
    python create_appimage.py <project_root>

Args:
    project_root: Path to the project root directory
"""

import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path


def create_appimage(project_root: Path):
    """
    Create AppImage from PyInstaller output.

    Args:
        project_root: Path to the project root directory

    Raises:
        RuntimeError: If AppImage creation fails
    """
    print("Creating Linux AppImage...")

    dist_dir = project_root / "dist"
    pyinstaller_output = dist_dir / "pikaraoke"
    appdir = dist_dir / "PiKaraoke.AppDir"

    if not pyinstaller_output.exists():
        raise RuntimeError(f"PyInstaller output not found at {pyinstaller_output}")

    if appdir.exists():
        print(f"Removing existing AppDir at {appdir}")
        shutil.rmtree(appdir)

    # Create AppDir structure
    appdir.mkdir(parents=True, exist_ok=True)
    usr_bin = appdir / "usr" / "bin"
    usr_share_icons = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    usr_share_applications = appdir / "usr" / "share" / "applications"

    usr_bin.mkdir(parents=True, exist_ok=True)
    usr_share_icons.mkdir(parents=True, exist_ok=True)
    usr_share_applications.mkdir(parents=True, exist_ok=True)

    print(f"Created AppDir structure at {appdir}")

    # Copy PyInstaller output to usr/bin
    print("Copying PyInstaller output to AppDir...")
    pikaraoke_bin = usr_bin / "pikaraoke"
    if pikaraoke_bin.exists():
        shutil.rmtree(pikaraoke_bin)

    shutil.copytree(pyinstaller_output, pikaraoke_bin)
    print(f"Copied PyInstaller output to {pikaraoke_bin}")

    # Copy icon
    copy_icon(project_root, appdir, usr_share_icons)

    # Copy desktop file
    copy_desktop_file(project_root, usr_share_applications)

    # Create AppRun script
    create_apprun(project_root, appdir)

    # Create symlinks for icon and .desktop file
    create_symlinks(appdir)

    # Download and run appimagetool
    appimage_path = run_appimagetool(appdir, dist_dir)

    print(f"AppImage created successfully at {appimage_path}")

    # Also create tar.gz as fallback
    create_tarball(pyinstaller_output, dist_dir)

    # Clean up intermediate directories
    cleanup_intermediate(appdir, pyinstaller_output, dist_dir)


def cleanup_intermediate(appdir: Path, pyinstaller_output: Path, dist_dir: Path):
    """
    Clean up intermediate directories after AppImage creation.

    Args:
        appdir: Path to the AppDir
        pyinstaller_output: Path to the PyInstaller output
        dist_dir: Path to the dist directory
    """
    print("Cleaning up intermediate directories...")

    intermediate_items = [
        appdir,
        pyinstaller_output,
        dist_dir / "pikaraoke_linux",
    ]

    for item in intermediate_items:
        if item.exists():
            try:
                shutil.rmtree(item)
                print(f"Removed intermediate directory: {item}")
            except Exception as e:
                print(f"Warning: Could not remove {item}: {e}")

    print("Cleanup completed")


def copy_icon(project_root: Path, appdir: Path, usr_share_icons: Path):
    """
    Copy icon file to AppDir.

    Args:
        project_root: Path to the project root
        appdir: Path to the AppDir
        usr_share_icons: Path to the icons directory
    """
    icon_source = project_root / "pikaraoke" / "logo.png"

    if not icon_source.exists():
        print(f"Warning: Icon not found at {icon_source}")
        return

    # Copy to usr/share/icons
    icon_dest = usr_share_icons / "pikaraoke.png"
    shutil.copy2(icon_source, icon_dest)

    # Also copy to root of AppDir
    appdir_icon = appdir / "pikaraoke.png"
    shutil.copy2(icon_source, appdir_icon)

    print(f"Copied icon to {icon_dest} and {appdir_icon}")


def copy_desktop_file(project_root: Path, usr_share_applications: Path):
    """
    Copy desktop entry file to AppDir.

    Args:
        project_root: Path to the project root
        usr_share_applications: Path to the applications directory
    """
    desktop_source = project_root / "build_scripts" / "linux" / "pikaraoke.desktop"

    if not desktop_source.exists():
        print(f"Warning: Desktop file not found at {desktop_source}")
        return

    desktop_dest = usr_share_applications / "pikaraoke.desktop"
    shutil.copy2(desktop_source, desktop_dest)

    print(f"Copied desktop file to {desktop_dest}")


def create_apprun(project_root: Path, appdir: Path):
    """
    Create AppRun script in AppDir.

    Args:
        project_root: Path to the project root
        appdir: Path to the AppDir
    """
    apprun_source = project_root / "build_scripts" / "linux" / "AppRun"
    apprun_dest = appdir / "AppRun"

    if apprun_source.exists():
        shutil.copy2(apprun_source, apprun_dest)
    else:
        # Create a basic AppRun script
        apprun_content = """#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
cd "${HERE}/usr/bin/pikaraoke"
exec "${HERE}/usr/bin/pikaraoke/pikaraoke" "$@"
"""
        with open(apprun_dest, "w", encoding="utf-8") as f:
            f.write(apprun_content)

    # Make AppRun executable
    apprun_dest.chmod(apprun_dest.stat().st_mode | stat.S_IEXEC)
    print(f"Created AppRun script at {apprun_dest}")


def create_symlinks(appdir: Path):
    """
    Create symlinks for icon and desktop file in AppDir root.

    Args:
        appdir: Path to the AppDir
    """
    desktop_link = appdir / "pikaraoke.desktop"
    desktop_target = appdir / "usr" / "share" / "applications" / "pikaraoke.desktop"

    if desktop_target.exists() and not desktop_link.exists():
        os.symlink(desktop_target.relative_to(appdir), desktop_link)
        print(f"Created symlink: {desktop_link} -> {desktop_target}")


def run_appimagetool(appdir: Path, dist_dir: Path) -> Path:
    """
    Download and run appimagetool to create AppImage.

    Args:
        appdir: Path to the AppDir
        dist_dir: Path to the dist directory

    Returns:
        Path to the created AppImage

    Raises:
        RuntimeError: If appimagetool fails
    """
    print("Downloading appimagetool...")

    appimagetool_path = dist_dir / "appimagetool-x86_64.AppImage"
    appimage_output = dist_dir / "pikaraoke-x86_64.AppImage"

    if not appimagetool_path.exists():
        url = "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
        try:
            urllib.request.urlretrieve(url, appimagetool_path)
            appimagetool_path.chmod(appimagetool_path.stat().st_mode | stat.S_IEXEC)
            print(f"Downloaded appimagetool to {appimagetool_path}")
        except Exception as e:
            raise RuntimeError(f"Failed to download appimagetool: {e}") from e
    else:
        print(f"Using existing appimagetool at {appimagetool_path}")

    if appimage_output.exists():
        appimage_output.unlink()

    print("Running appimagetool...")
    try:
        env = os.environ.copy()
        env["ARCH"] = "x86_64"

        result = subprocess.run(
            [str(appimagetool_path), str(appdir), str(appimage_output)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running appimagetool: {e}")
        print(e.stderr)
        raise RuntimeError("AppImage creation failed") from e

    if not appimage_output.exists():
        raise RuntimeError(f"AppImage not created at {appimage_output}")

    return appimage_output


def create_tarball(pyinstaller_output: Path, dist_dir: Path):
    """
    Create a tar.gz archive as a fallback distribution method.

    Args:
        pyinstaller_output: Path to the PyInstaller output
        dist_dir: Path to the dist directory
    """
    print("Creating tar.gz archive as fallback...")

    linux_dir = dist_dir / "pikaraoke_linux"
    if linux_dir.exists():
        shutil.rmtree(linux_dir)

    shutil.copytree(pyinstaller_output, linux_dir)

    tarball_path = dist_dir / "pikaraoke_linux.tar.gz"
    if tarball_path.exists():
        tarball_path.unlink()

    try:
        subprocess.run(
            ["tar", "-czvf", str(tarball_path), "-C", str(dist_dir), linux_dir.name],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"Created tar.gz at {tarball_path}")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not create tar.gz: {e}")


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 2:
        print("Usage: python create_appimage.py <project_root>")
        return 1

    project_root = Path(sys.argv[1]).resolve()

    if not project_root.exists():
        print(f"Error: Project root does not exist: {project_root}")
        return 1

    try:
        create_appimage(project_root)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

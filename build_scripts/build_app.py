"""
Unified cross-platform build script for PiKaraoke.

This script handles building PiKaraoke binaries for Windows, macOS, and Linux
using PyInstaller and platform-specific packaging tools.

Usage:
    python build_app.py [--platform PLATFORM] [--debug] [--stage STAGE]

Args:
    --platform: Target platform (Windows, Darwin, Linux). Auto-detected if not specified.
    --debug: Enable verbose logging
    --stage: Build only specific stage (pyinstaller, package, all)
"""

import argparse
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


class BuildConfig:
    """Configuration for the build process."""

    def __init__(self, project_root: Path, target_platform: str, debug: bool = False):
        """
        Initialize build configuration.

        Args:
            project_root: Path to the project root directory
            target_platform: Target platform (Windows, Darwin, Linux)
            debug: Enable debug logging
        """
        self.project_root = project_root
        self.target_platform = target_platform
        self.debug = debug

        self.build_scripts_dir = project_root / "build_scripts"
        self.dist_dir = project_root / "dist"
        self.build_dir = project_root / "build"
        self.spec_file = self.build_scripts_dir / "common" / "pikaraoke.spec"

        self.version = self._get_version()

    def _get_version(self) -> str:
        """
        Extract version from pyproject.toml.

        Returns:
            Version string
        """
        pyproject_path = self.project_root / "pyproject.toml"
        try:
            with open(pyproject_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version ="):
                        version = line.split("=")[1].strip().strip('"').strip("'")
                        return version
        except Exception as e:
            logging.warning(f"Could not extract version from pyproject.toml: {e}")
            return "0.0.0"
        return "0.0.0"


class BuildOrchestrator:
    """Orchestrates the build process across different platforms."""

    def __init__(self, config: BuildConfig):
        """
        Initialize build orchestrator.

        Args:
            config: Build configuration
        """
        self.config = config
        self._setup_logging()

    def _setup_logging(self):
        """Set up logging configuration."""
        level = logging.DEBUG if self.config.debug else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%H:%M:%S",
        )

    def run(self, stage: str = "all"):
        """
        Run the build process.

        Args:
            stage: Build stage to run (pyinstaller, package, all)

        Raises:
            ValueError: If stage is invalid
            RuntimeError: If build fails
        """
        logging.info("=" * 60)
        logging.info(f"PiKaraoke Build Script - {self.config.target_platform}")
        logging.info(f"Version: {self.config.version}")
        logging.info("=" * 60)

        if stage in ("all", "pyinstaller"):
            self._run_pyinstaller()

        if stage in ("all", "package"):
            self._run_packaging()

        logging.info("=" * 60)
        logging.info("Build completed successfully!")
        logging.info("=" * 60)

    def _run_pyinstaller(self):
        """Run PyInstaller to create the application bundle."""
        logging.info("Running PyInstaller...")

        if not self.config.spec_file.exists():
            raise RuntimeError(f"Spec file not found: {self.config.spec_file}")

        cmd = [
            "pyinstaller",
            str(self.config.spec_file),
            "--clean",
            "--noconfirm",
            f"--distpath={self.config.dist_dir}",
            f"--workpath={self.config.build_dir}",
        ]

        logging.debug(f"Running command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, cwd=self.config.project_root, check=True, capture_output=True, text=True
            )
            if self.config.debug:
                logging.debug(result.stdout)
        except subprocess.CalledProcessError as e:
            logging.error(f"PyInstaller failed: {e}")
            logging.error(e.stderr)
            raise RuntimeError("PyInstaller build failed") from e

        self._verify_pyinstaller_output()
        logging.info("PyInstaller build completed successfully")

    def _verify_pyinstaller_output(self):
        """Verify PyInstaller created the expected output."""
        expected_dir = self.config.dist_dir / "pikaraoke"

        if not expected_dir.exists():
            raise RuntimeError(f"PyInstaller output not found at {expected_dir}")

        if self.config.target_platform == "Windows":
            exe_path = expected_dir / "pikaraoke.exe"
            if not exe_path.exists():
                raise RuntimeError(f"pikaraoke.exe not found at {exe_path}")
        else:
            exe_path = expected_dir / "pikaraoke"
            if not exe_path.exists():
                raise RuntimeError(f"pikaraoke executable not found at {exe_path}")

        logging.info(f"Verified PyInstaller output at {expected_dir}")

    def _run_packaging(self):
        """Run platform-specific packaging."""
        logging.info(f"Running {self.config.target_platform} packaging...")

        if self.config.target_platform == "Windows":
            self._package_windows()
        elif self.config.target_platform == "Darwin":
            self._package_macos()
        elif self.config.target_platform == "Linux":
            self._package_linux()
        else:
            raise ValueError(f"Unsupported platform: {self.config.target_platform}")

    def _package_windows(self):
        """Package Windows application."""
        logging.info("Creating Windows portable package...")

        portable_dir = self.config.dist_dir / "pikaraoke_portable"
        source_dir = self.config.dist_dir / "pikaraoke"

        if portable_dir.exists():
            shutil.rmtree(portable_dir)

        shutil.copytree(source_dir, portable_dir)
        logging.info(f"Created portable directory at {portable_dir}")

        zip_path = self.config.dist_dir / "pikaraoke_win_portable.zip"
        if zip_path.exists():
            zip_path.unlink()

        shutil.make_archive(
            str(self.config.dist_dir / "pikaraoke_win_portable"),
            "zip",
            portable_dir.parent,
            portable_dir.name,
        )
        logging.info(f"Created portable ZIP at {zip_path}")

        logging.info("Running Inno Setup to create installer...")
        self._run_inno_setup()

        self._cleanup_windows_intermediate()

    def _cleanup_windows_intermediate(self):
        """Clean up intermediate directories after Windows packaging."""
        logging.info("Cleaning up intermediate directories...")

        intermediate_dirs = [
            self.config.dist_dir / "pikaraoke",
            self.config.dist_dir / "pikaraoke_portable",
        ]

        for dir_path in intermediate_dirs:
            if dir_path.exists():
                try:
                    shutil.rmtree(dir_path)
                    logging.debug(f"Removed intermediate directory: {dir_path}")
                except Exception as e:
                    logging.warning(f"Could not remove {dir_path}: {e}")

        logging.info("Cleanup completed")

    def _run_inno_setup(self):
        """Run Inno Setup to create Windows installer."""
        iss_file = self.config.build_scripts_dir / "windows" / "installer.iss"

        if not iss_file.exists():
            raise RuntimeError(f"Inno Setup script not found: {iss_file}")

        iscc_paths = [
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"))
            / "Inno Setup 6"
            / "ISCC.exe",
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Inno Setup 6" / "ISCC.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"))
            / "Inno Setup 5"
            / "ISCC.exe",
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Inno Setup 5" / "ISCC.exe",
        ]

        iscc_path = None
        for path in iscc_paths:
            if path.exists():
                iscc_path = path
                break

        if not iscc_path:
            raise RuntimeError(
                "Inno Setup (ISCC.exe) not found. Please install from https://jrsoftware.org/isdl.php"
            )

        cmd = [str(iscc_path), f"/DMyAppVersion={self.config.version}", str(iss_file)]

        logging.debug(f"Running command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, cwd=iss_file.parent, check=True, capture_output=True, text=True
            )
            if self.config.debug:
                logging.debug(result.stdout)
        except subprocess.CalledProcessError as e:
            logging.error(f"Inno Setup failed: {e}")
            logging.error(e.stderr)
            raise RuntimeError("Inno Setup build failed") from e

        installer_dir = self.config.dist_dir / "installer"
        installer_files = list(installer_dir.glob("PiKaraoke-Setup-*.exe"))

        if not installer_files:
            raise RuntimeError(f"Installer not found in {installer_dir}")

        logging.info(f"Created installer: {installer_files[0]}")

    def _package_macos(self):
        """Package macOS application."""
        logging.info("Creating macOS .app bundle and DMG...")

        macos_script = self.config.build_scripts_dir / "macos" / "create_app_bundle.py"

        if not macos_script.exists():
            raise RuntimeError(f"macOS packaging script not found: {macos_script}")

        cmd = [sys.executable, str(macos_script), str(self.config.project_root)]

        logging.debug(f"Running command: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if self.config.debug:
                logging.debug(result.stdout)
        except subprocess.CalledProcessError as e:
            logging.error(f"macOS packaging failed: {e}")
            logging.error(e.stderr)
            raise RuntimeError("macOS packaging failed") from e

        logging.info("macOS packaging completed successfully")

    def _package_linux(self):
        """Package Linux application."""
        logging.info("Creating Linux AppImage...")

        linux_script = self.config.build_scripts_dir / "linux" / "create_appimage.py"

        if not linux_script.exists():
            raise RuntimeError(f"Linux packaging script not found: {linux_script}")

        cmd = [sys.executable, str(linux_script), str(self.config.project_root)]

        logging.debug(f"Running command: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if self.config.debug:
                logging.debug(result.stdout)
        except subprocess.CalledProcessError as e:
            logging.error(f"Linux packaging failed: {e}")
            logging.error(e.stderr)
            raise RuntimeError("Linux packaging failed") from e

        logging.info("Linux packaging completed successfully")


def parse_args():
    """
    Parse command line arguments.

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Build PiKaraoke for multiple platforms",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--platform",
        type=str,
        choices=["Windows", "Darwin", "Linux"],
        default=platform.system(),
        help="Target platform (default: auto-detect)",
    )

    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")

    parser.add_argument(
        "--stage",
        type=str,
        choices=["pyinstaller", "package", "all"],
        default="all",
        help="Build stage to run (default: all)",
    )

    return parser.parse_args()


def main():
    """Main entry point for the build script."""
    args = parse_args()

    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent

    try:
        config = BuildConfig(project_root, args.platform, args.debug)
        orchestrator = BuildOrchestrator(config)
        orchestrator.run(args.stage)
        return 0
    except Exception as e:
        logging.error(f"Build failed: {e}")
        if args.debug:
            logging.exception("Full traceback:")
        return 1


if __name__ == "__main__":
    sys.exit(main())

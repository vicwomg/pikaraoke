# Building PiKaraoke

This guide explains how to build PiKaraoke binaries for Windows, macOS, and Linux.

## Overview

PiKaraoke uses a unified build system based on:

- **PyInstaller** for bundling Python applications
- **uv** for fast dependency management
- **Platform-specific packaging tools** for creating installers

The build system produces:

- **Windows**: `.exe` installer (via Inno Setup) and portable `.zip`
- **macOS**: `.dmg` disk image and `.zip` archive
- **Linux**: `.AppImage` and `.tar.gz` archive

## Prerequisites

### All Platforms

1. **Python 3.10 or higher**
2. **uv package manager**
   ```bash
   # Install uv
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **Git** (to clone the repository)

### Windows-Specific

- **Inno Setup 6.x** (for creating installers)
  - Download from: https://jrsoftware.org/isdl.php
  - Install to default location

### macOS-Specific

- **Xcode Command Line Tools**
  ```bash
  xcode-select --install
  ```
- **create-dmg** (optional, for better DMG appearance)
  ```bash
  brew install create-dmg
  ```

### Linux-Specific

- **Standard build tools**
  ```bash
  # Ubuntu/Debian
  sudo apt-get update
  sudo apt-get install build-essential fuse
  ```

## Quick Start

### Clone and Setup

```bash
git clone https://github.com/safepay/pikaraoke.git
cd pikaraoke
uv sync
```

### Build for Your Platform

```bash
uv run python build_scripts/build_app.py
```

This will:

1. Run PyInstaller to bundle the application
2. Create platform-specific packages
3. Automatically clean up intermediate files
4. Output distributable files to the `dist/` directory

## Build Output

### Automatic Cleanup

The build system **automatically cleans up intermediate directories** after successful packaging. Only distributable files remain in `dist/`.

**Intermediate files removed:**

- `dist/pikaraoke/` - PyInstaller raw output
- `dist/pikaraoke_portable/` - Windows temporary copy
- `dist/PiKaraoke.AppDir/` - Linux AppImage build directory
- `dist/pikaraoke_linux/` - Linux tarball temporary directory

**Final distributable files:**

### Windows

After building, you will find:

```
dist/
├── pikaraoke_win_portable.zip          # Portable version (extract and run)
└── installer/
    └── PiKaraoke-Setup-{version}.exe   # Installer (recommended)
```

**Installer Features:**

- Includes FFmpeg (optional component)
- Creates Start Menu shortcuts
- Configurable songs directory
- Automatic updates to PATH

### macOS

After building, you will find:

```
dist/
├── PiKaraoke.app/                      # macOS application bundle
├── pikaraoke_mac.zip                   # ZIP archive (for sharing)
└── pikaraoke_mac.dmg                   # DMG disk image (recommended)
```

**Installation:**

- Open the DMG
- Drag PiKaraoke.app to Applications folder
- First launch: Right-click → Open (to bypass Gatekeeper)

### Linux

After building, you will find:

```
dist/
├── pikaraoke-x86_64.AppImage           # AppImage (recommended)
└── pikaraoke_linux.tar.gz              # Tarball (alternative)
```

**AppImage Usage:**

```bash
chmod +x pikaraoke-x86_64.AppImage
./pikaraoke-x86_64.AppImage
```

**If FUSE is not available:**

```bash
./pikaraoke-x86_64.AppImage --appimage-extract-and-run
```

## Advanced Usage

### Build Stages

Build only specific stages:

```bash
# Only run PyInstaller
uv run python build_scripts/build_app.py --stage pyinstaller

# Only run packaging (assumes PyInstaller already ran)
uv run python build_scripts/build_app.py --stage package
```

### Debug Mode

Enable verbose logging:

```bash
uv run python build_scripts/build_app.py --debug
```

### Cross-Platform Considerations

You can only build for your current platform. Cross-compilation is not supported:

- Build Windows binaries on Windows
- Build macOS binaries on macOS
- Build Linux binaries on Linux

## Continuous Integration

The project uses GitHub Actions for automated builds. See [.github/workflows/build-all-binaries.yml](../.github/workflows/build-all-binaries.yml).

### Triggering a Build

1. Go to the **Actions** tab in GitHub
2. Select **Build All Binaries** workflow
3. Click **Run workflow**
4. Wait for builds to complete
5. Download artifacts from the workflow run

## Build System Architecture

### Directory Structure

```
build_scripts/
├── build_app.py              # Main build orchestrator
├── common/
│   └── pikaraoke.spec        # PyInstaller spec file
├── macos/
│   └── create_app_bundle.py  # macOS packaging
├── linux/
│   ├── create_appimage.py    # Linux packaging
│   ├── pikaraoke.desktop     # Desktop entry
│   └── AppRun                # AppImage launcher
└── windows/
    └── installer.iss         # Inno Setup script
```

### Build Process

1. **Dependency Installation** (`uv sync`)

   - Installs all Python dependencies from `pyproject.toml`
   - Uses lockfile for reproducible builds

2. **PyInstaller Build**

   - Bundles Python interpreter and dependencies
   - Creates one-directory build (not single-file)
   - Collects data files (templates, static assets)
   - Output: `dist/pikaraoke/` directory

3. **Platform-Specific Packaging**

   - **Windows**: Creates portable ZIP, then runs Inno Setup
   - **macOS**: Creates .app bundle, signs it, creates DMG
   - **Linux**: Creates AppImage and tar.gz

## Troubleshooting

### Windows: "Inno Setup not found"

Ensure Inno Setup is installed to the default location:

```
C:\Program Files (x86)\Inno Setup 6\
```

Or install via Chocolatey:

```powershell
choco install innosetup
```

### macOS: "Code signing failed"

This is a warning only. Ad-hoc signing is used for local builds. For distribution, you need:

1. Apple Developer account
2. Valid signing certificate
3. Update build script with your identity

### Linux: "appimagetool not found"

The script automatically downloads `appimagetool`. If this fails:

1. Check internet connectivity
2. Download manually from: https://github.com/AppImage/AppImageKit/releases
3. Place in `dist/` directory as `appimagetool-x86_64.AppImage`
4. Make executable: `chmod +x dist/appimagetool-x86_64.AppImage`

### Build Size Too Large

The build includes yt-dlp with 2000+ video extractors. To reduce size:

1. Consider excluding unused extractors in the spec file
2. Use UPX compression (already enabled)
3. Review dependencies in `pyproject.toml`

### Missing FFmpeg

**Windows**: The GitHub Actions workflow automatically includes FFmpeg. For local builds:

1. Download from: https://www.gyan.dev/ffmpeg/builds/
2. Extract `ffmpeg.exe`
3. Place in `build/ffmpeg/ffmpeg.exe`

**macOS/Linux**: Install FFmpeg via package manager:

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# Fedora
sudo dnf install ffmpeg
```

## Development Workflow

### Testing Local Changes

```bash
# Make your code changes
git add .
git commit -m "Your changes"

# Build locally
uv run python build_scripts/build_app.py

# Test the build
# Windows: Run dist/pikaraoke_portable/pikaraoke.exe
# macOS: Open dist/PiKaraoke.app
# Linux: Run dist/pikaraoke-x86_64.AppImage
```

### Creating a Release

1. Update version in `pyproject.toml`
2. Commit and tag:
   ```bash
   git commit -am "Bump version to X.Y.Z"
   git tag vX.Y.Z
   git push origin master --tags
   ```
3. Trigger GitHub Actions build
4. Download artifacts
5. Create GitHub release with binaries

## Build Script Reference

### build_app.py

Main build orchestrator. Coordinates PyInstaller and platform-specific packaging.

**Options:**

- `--platform`: Target platform (auto-detected by default)
- `--debug`: Enable verbose logging
- `--stage`: Run specific stage (pyinstaller, package, all)

**Environment Variables:**

- `SPECPATH`: Set by PyInstaller (path to spec file)
- `PYTHONPATH`: May need adjustment for imports

### pikaraoke.spec

PyInstaller specification file. Defines:

- Entry point (`pikaraoke/app.py`)
- Data files to include
- Hidden imports for runtime
- Exclusions to reduce size

**Key Sections:**

- `datas`: Static files (templates, CSS, images)
- `hiddenimports`: Runtime dependencies
- `excludes`: Packages to skip (tkinter, numpy, etc.)

## Performance Optimization

### Build Time

Typical build times:

- **PyInstaller**: 3-5 minutes
- **Windows packaging**: 1-2 minutes
- **macOS packaging**: 2-3 minutes
- **Linux packaging**: 4-6 minutes (AppImage creation)

### Caching

GitHub Actions caches:

- uv dependencies
- PyInstaller build cache

Local builds cache:

- `build/` directory (PyInstaller temp files)
- `.uv/` directory (uv cache)

To clean cache:

```bash
rm -rf build/ dist/ .uv/
```

## Contributing

When modifying the build system:

1. Test on all three platforms
2. Update this documentation
3. Ensure backward compatibility
4. Run pre-commit hooks:
   ```bash
   pre-commit run --all-files
   ```

## Support

For build issues:

1. Check this documentation
2. Review GitHub Actions logs
3. Open an issue: https://github.com/safepay/pikaraoke/issues

## License

The build scripts are part of PiKaraoke and use the same license as the main project.

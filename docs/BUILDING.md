# Building PiKaraoke

This guide explains how to build PiKaraoke binaries for Windows, macOS, and Linux using
Briefcase.

## Overview

PiKaraoke uses [BeeWare Briefcase](https://briefcase.readthedocs.io/) for cross-platform
packaging. Briefcase creates native installers for each platform with a simple, unified build
process.

The build system produces:

- **Windows**: `.msi` installer
- **macOS**: `.dmg` disk image (universal binary for Intel + Apple Silicon)
- **Linux**: `.AppImage` portable executable

## Prerequisites

### All Platforms

1. **Python 3.10 or higher**
2. **uv package manager**
   ```bash
   # Install uv
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # or on Windows (PowerShell)
   irm https://astral.sh/uv/install.ps1 | iex
   ```
3. **Git**
4. **Briefcase**
   ```bash
   pip install briefcase
   # or with uv (faster)
   uv pip install briefcase
   ```

### Platform-Specific Notes

- **Windows**: Builds produce MSI installers
- **macOS**: Builds produce universal binaries (Intel + Apple Silicon)
- **Linux**: Builds produce AppImages (requires Ubuntu 20.04 or later recommended)

## Quick Start

### Clone and Setup

```bash
git clone https://github.com/vicwomg/pikaraoke.git
cd pikaraoke
uv sync
```

### Build for Your Platform

```bash
# Create platform scaffold (first time only)
briefcase create

# Build the application
briefcase build

# Test the built application (optional)
briefcase run

# Create distributable package
briefcase package
```

The distributable installer will be in the `dist/` directory.

## Build Output

### Windows

After `briefcase package windows`:

```
dist/
└── PiKaraoke-1.16.0.msi
```

### macOS

After `briefcase package macOS`:

```
dist/
└── PiKaraoke-1.16.0.dmg
```

### Linux

After `briefcase package linux`:

```
dist/
└── PiKaraoke-1.16.0.AppImage
```

## GitHub Actions CI/CD

### Running the Workflow

1. Go to the **Actions** tab in GitHub
2. Select **Build All Binaries** workflow
3. Click **Run workflow** button
4. Select the branch (usually `master`)
5. Click **Run workflow**
6. Wait for builds to complete (15-20 minutes for all platforms)

### Downloading Artifacts

After the workflow completes:

1. Click on the completed workflow run
2. Scroll down to **Artifacts** section
3. Download each platform artifact:
   - `PiKaraoke-windows-latest`
   - `PiKaraoke-macos-latest`
   - `PiKaraoke-ubuntu-20.04`

## Creating a Release

After downloading the workflow artifacts:

### 1. Test the Installers

Test each installer on its target platform to ensure it works correctly:

- Launch the app
- Verify the web interface loads
- Test basic functionality (search, queue, playback)
- Check that FFmpeg warning appears if not installed

### 2. Create GitHub Release

1. Go to **Releases** in your GitHub repository
2. Click **Draft a new release**
3. Tag version: `v1.16.0` (match version in pyproject.toml)
4. Release title: `PiKaraoke 1.16.0`
5. Add release notes describing changes

### 3. Attach Release Assets

Rename and attach the installers:

- `PiKaraoke-1.16.0-windows.msi` (from Windows artifact)
- `PiKaraoke-1.16.0-macos.dmg` (from macOS artifact)
- `PiKaraoke-1.16.0-linux.AppImage` (from Linux artifact)

### 4. Update Documentation

Add download links to:

- Project README.md
- Wiki download page
- Release announcement

## FFmpeg Requirements

**Important**: FFmpeg is **NOT bundled** with installers. Users must install it separately.

Include this in all release notes:

### System Requirements

PiKaraoke requires FFmpeg to be installed separately:

- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` (or equivalent)
- **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/) and add to PATH

## Troubleshooting

### Build fails with module not found

Ensure dependencies are installed:

```bash
uv sync
uv pip install briefcase
```

### Windows: MSI not created

Check that the build completed successfully:

```bash
briefcase package windows -v
```

The `-v` flag provides verbose output for debugging.

### Windows/macOS: Code signing issues

For local builds without a signing certificate, use ad-hoc signing:

```bash
# Windows
briefcase package windows --adhoc-sign

# macOS
briefcase package macOS --adhoc-sign
```

For distribution with proper code signing:

1. **Windows**: Obtain a code signing certificate
2. **macOS**: Need Apple Developer account and signing certificate
3. Configure signing identity in pyproject.toml
4. Package without `--adhoc-sign` flag

### Linux: AppImage won't run

Make the AppImage executable:

```bash
chmod +x PiKaraoke-1.16.0.AppImage
```

If FUSE is not available:

```bash
./PiKaraoke-1.16.0.AppImage --appimage-extract-and-run
```

### App launches but shows FFmpeg error

This is expected. FFmpeg must be installed separately on the user's system.

## Development Workflow

### Testing Local Changes

```bash
# Make your code changes
git add .
git commit -m "Your changes"

# Build and test locally
briefcase create
briefcase build
briefcase run

# Create package when satisfied
briefcase package
```

### Version Updates

Before creating a release:

1. Update version in `pyproject.toml` (both `[project]` and `[tool.briefcase]` sections)
2. Update CHANGELOG or release notes
3. Commit changes:
   ```bash
   git commit -am "Bump version to X.Y.Z"
   git tag vX.Y.Z
   git push origin master --tags
   ```
4. Trigger GitHub Actions build

## Configuration

All build configuration is in [pyproject.toml](../pyproject.toml) under `[tool.briefcase]`.

Key configuration sections:

- `[tool.briefcase]`: Global settings (version, bundle ID)
- `[tool.briefcase.app.pikaraoke]`: App-specific settings (name, icon, dependencies)
- `[tool.briefcase.app.pikaraoke.macOS]`: macOS-specific settings
- `[tool.briefcase.app.pikaraoke.windows]`: Windows-specific settings
- `[tool.briefcase.app.pikaraoke.linux]`: Linux-specific settings

## Advanced Usage

### Verbose Build Output

```bash
briefcase package -v
```

### Update Existing Build

If you've already created a build and want to update it:

```bash
briefcase update
briefcase build
briefcase package
```

### Clean Build

To start fresh:

```bash
rm -rf build/ dist/
briefcase create
briefcase package
```

### Platform-Specific Builds

You can only build for your current platform. To build for all platforms, use the GitHub
Actions workflow or set up build environments for each platform.

## Performance Notes

Typical build times (after dependencies installed):

- **First-time create**: 5-10 minutes
- **Build**: 2-3 minutes
- **Package**: 3-5 minutes
- **Total**: 10-18 minutes per platform

GitHub Actions builds all three platforms in parallel (~20 minutes total).

## Migrating from PyInstaller

This project previously used PyInstaller for builds. The migration to Briefcase:

- Reduced build code by ~93% (1,400+ lines → 90 lines)
- Simplified maintenance (single configuration file)
- Eliminated platform-specific tooling requirements
- Unified build commands across platforms

For migration details, see [BRIEFCASE_MIGRATION.md](BRIEFCASE_MIGRATION.md).

## Support

For build issues:

1. Check this documentation
2. Review [Briefcase documentation](https://briefcase.readthedocs.io/)
3. Check GitHub Actions logs for CI builds
4. Open an issue: https://github.com/vicwomg/pikaraoke/issues

## Contributing

When modifying the build configuration:

1. Test on your platform locally
2. Update this documentation
3. Test via GitHub Actions before merging
4. Run pre-commit hooks:
   ```bash
   pre-commit run --all-files --config code_quality/.pre-commit-config.yaml
   ```

## License

The build configuration is part of PiKaraoke and uses the same license as the main project.

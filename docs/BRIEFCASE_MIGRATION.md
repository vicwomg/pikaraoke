# Briefcase Build System

PiKaraoke uses [BeeWare Briefcase](https://briefcase.readthedocs.io/) to create native
installers for Windows, macOS, and Linux. This replaced the previous PyInstaller-based build
system, reducing build code by ~93% (from 1,400+ lines to ~90 lines of configuration).

## Quick Start: Local Building

### Prerequisites

Install Briefcase:

```bash
pip install briefcase
# or with uv (faster)
uv pip install briefcase
```

### Build Commands

Build for your current platform:

```bash
# Create platform scaffold (first time only)
briefcase create

# Build the application
briefcase build

# Test the built application
briefcase run

# Create distributable package
briefcase package
```

Platform-specific outputs:

- **Windows**: `dist/PiKaraoke-1.16.0.msi`
- **macOS**: `dist/PiKaraoke-1.16.0.dmg`
- **Linux**: `dist/PiKaraoke-1.16.0.AppImage`

## GitHub Actions Workflow

The [build-all-binaries.yml](../.github/workflows/build-all-binaries.yml) workflow automatically
builds installers for all platforms when manually triggered.

### Running the Workflow

1. Go to **Actions** tab in GitHub
2. Select **Build All Binaries** workflow
3. Click **Run workflow**
4. Download artifacts from the workflow run

### Workflow Outputs

Each platform job produces installer artifacts:

- **Windows**: MSI installer
- **macOS**: DMG disk image (universal binary for Intel + Apple Silicon)
- **Linux**: AppImage (portable executable)

## Distribution

After a successful workflow run:

1. Download the artifacts from the GitHub Actions run
2. Test each installer on its target platform
3. Create a GitHub Release and attach the installers
4. Update the Wiki or README with download links

Suggested release asset naming:

- `PiKaraoke-1.16.0-windows.msi`
- `PiKaraoke-1.16.0-macos.dmg`
- `PiKaraoke-1.16.0-linux.AppImage`

## FFmpeg Requirements

**Important**: FFmpeg is NOT bundled with the installers. Users must install FFmpeg separately:

- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` (or equivalent)
- **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/)

Document this clearly in release notes and the main README.

## Configuration

All Briefcase configuration is in [pyproject.toml](../pyproject.toml) under the
`[tool.briefcase]` section. Key settings:

- **Entry point**: Uses [pikaraoke/\_\_main\_\_.py](../pikaraoke/__main__.py)
- **Icon**: Auto-converts `pikaraoke/static/icons/logo.png` to platform-specific formats
- **Version**: Synchronized with `[project]` version
- **Dependencies**: Listed in `requires` array

## Troubleshooting

### App doesn't launch

Check that FFmpeg is installed and accessible in system PATH:

```bash
ffmpeg -version
```

### Build fails

Ensure you have the latest Briefcase version:

```bash
pip install --upgrade briefcase
```

### Resources not found

Briefcase bundles the entire `pikaraoke/` directory. Static files and templates should work
without changes.

## Resources

- [Briefcase Documentation](https://briefcase.readthedocs.io/)
- [BeeWare Project](https://beeware.org/)
- [Configuration Reference](https://briefcase.readthedocs.io/en/latest/reference/configuration.html)

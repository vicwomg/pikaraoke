#!/bin/bash

# PiKaraoke One-Line Installer
# Supports macOS (Homebrew) and Linux (apt-get)

set -e
set -o pipefail

# Handle flags
CONFIRM="y"
LOCAL="n"
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -y|--yes) CONFIRM="n" ;;
        -l|--local) LOCAL="y" ;; # this installs pikaraoke from local source
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Function to check if Python is compatible
is_python_compatible() {
    local python_cmd
    if command -v python3 &> /dev/null; then
        python_cmd="python3"
    elif command -v python &> /dev/null; then
        python_cmd="python"
    else
        return 1
    fi

    local version
    version=$($python_cmd -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    local major
    major=$(echo "$version" | cut -d. -f1)
    local minor
    minor=$(echo "$version" | cut -d. -f2)

    if [ "$major" -lt 3 ] || ([ "$major" -eq 3 ] && [ "$minor" -lt 10 ]); then
        return 1
    fi
    return 0
}

# Detect OS
OS_TYPE="$(uname -s)"
echo "--- PiKaraoke Installer ---"
echo "Detected OS: $OS_TYPE"

# Determine packages to install
PKGS_TO_INSTALL=()
DISPLAY_PKGS=()

if [ "$OS_TYPE" == "Darwin" ] && ! command -v brew &> /dev/null; then DISPLAY_PKGS+=("Homebrew"); fi

if ! is_python_compatible; then
    if [ "$OS_TYPE" == "Darwin" ]; then
        PKGS_TO_INSTALL+=("python"); DISPLAY_PKGS+=("python")
    else
        PKGS_TO_INSTALL+=("python3"); DISPLAY_PKGS+=("python3")
    fi
fi

if ! command -v ffmpeg &> /dev/null; then
    if [ "$OS_TYPE" == "Darwin" ]; then
        PKGS_TO_INSTALL+=("ffmpeg-full"); DISPLAY_PKGS+=("ffmpeg")
    else
        PKGS_TO_INSTALL+=("ffmpeg"); DISPLAY_PKGS+=("ffmpeg")
    fi
fi

SKIP_UV=0
if command -v uv &> /dev/null; then
    SKIP_UV=1
else
    PKGS_TO_INSTALL+=("uv"); DISPLAY_PKGS+=("uv")
fi

SKIP_DENO=0
if command -v node &> /dev/null || command -v deno &> /dev/null; then
    SKIP_DENO=1
else
    DISPLAY_PKGS+=("deno")
    if [ "$OS_TYPE" == "Darwin" ]; then PKGS_TO_INSTALL+=("deno"); fi
fi

DISPLAY_LIST=$(IFS=", "; echo "${DISPLAY_PKGS[*]}")
if [ -z "$DISPLAY_LIST" ]; then
    INSTALL_LIST="pikaraoke (via uv)"
else
    INSTALL_LIST="$DISPLAY_LIST, pikaraoke (via uv)"
fi

echo "The following packages will be installed/updated: $INSTALL_LIST"
if [ "$CONFIRM" == "y" ]; then
    read -p "Do you want to proceed? (y/n) " -r < /dev/tty
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Installation cancelled."
        exit 1
    fi
fi

INSTALL_SHORTCUTS=0
if [ "$CONFIRM" == "y" ]; then
    read -p "Do you want to install desktop shortcuts? (y/n) " -r < /dev/tty
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        INSTALL_SHORTCUTS=1
    fi
    echo
else
    # In non-interactive mode, we default to skipping shortcuts to be safe
    # or we could default to 1 if we want them. Usually CI doesn't need shortcuts.
    INSTALL_SHORTCUTS=0
fi

if [ "$OS_TYPE" == "Darwin" ]; then
    # macOS
    if ! command -v brew &> /dev/null; then
        echo "Homebrew not found. Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to path for the current session
        if [[ $(uname -m) == "arm64" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        else
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    fi

    if [ ${#PKGS_TO_INSTALL[@]} -gt 0 ]; then
        echo "Installing dependencies via Homebrew: ${PKGS_TO_INSTALL[*]}"
        brew install "${PKGS_TO_INSTALL[@]}"
    else
        echo "All core dependencies (Python, FFmpeg, uv) are already installed."
    fi

    # link ffmpeg-full to path since it is keg-only
    if [[ " ${PKGS_TO_INSTALL[*]} " =~ " ffmpeg-full " ]] || ! command -v ffmpeg &> /dev/null; then
        brew link ffmpeg-full
    fi

elif [ "$OS_TYPE" == "Linux" ]; then
    # Linux (Assumes Debian/Ubuntu/Raspberry Pi OS)
    if ! command -v apt-get &> /dev/null; then
        echo "Error: This script currently only supports Debian-based Linux distributions (using apt-get)."
        exit 1
    fi

    echo "Checking for missing dependencies..."
    if [ ${#PKGS_TO_INSTALL[@]} -gt 0 ]; then
        echo "Updating package lists..."
        sudo apt-get update
        echo "Installing dependencies via apt: ${PKGS_TO_INSTALL[*]}"
        # Special handling for deno if it was in the list but needs curl install
        APT_PKGS=()
        for pkg in "${PKGS_TO_INSTALL[@]}"; do
            if [ "$pkg" != "deno" ] && [ "$pkg" != "uv" ]; then
                APT_PKGS+=("$pkg")
            fi
        done
        if [ ${#APT_PKGS[@]} -gt 0 ]; then
            sudo apt-get install -y "${APT_PKGS[@]}"
        fi
    else
        echo "All core dependencies (Python, FFmpeg) are already installed."
    fi

    if [ $SKIP_UV -eq 0 ] && ! command -v uv &> /dev/null; then
        echo "Installing uv..."
        curl -fsSL https://astral.sh/uv/install.sh | sh
        # Add uv to PATH for the current session. Default install is ~/.local/bin or ~/.cargo/bin
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    fi

    if [ $SKIP_DENO -eq 0 ] && ! command -v deno &> /dev/null; then
        echo "Installing Deno..."
        curl -fsSL https://deno.land/install.sh | sh
        # Add Deno to PATH for the current session
        export DENO_INSTALL="$HOME/.deno"
        export PATH="$DENO_INSTALL/bin:$PATH"
    fi
else
    echo "Error: Unsupported OS ($OS_TYPE)"
    exit 1
fi

# Final check
if ! is_python_compatible; then
    echo "Error: Failed to find or install a compatible Python version (3.10+)."
    exit 1
fi

# Install pikaraoke
echo "Installing pikaraoke via uv..."

if uv tool list | grep -q "pikaraoke"; then
    echo "PiKaraoke is already installed. Upgrading..."
    if [ "$LOCAL" == "y" ]; then
        uv tool install --force .
    else
        uv tool upgrade pikaraoke
    fi
else
    if [ "$LOCAL" == "y" ]; then
        uv tool install .
    else
        uv tool install pikaraoke
    fi
fi

# 6. Create Desktop Shortcuts
if [ $INSTALL_SHORTCUTS -eq 1 ]; then
    echo "Creating Desktop Shortcuts..."
    PIKARAOKE_BIN=$(command -v pikaraoke || echo "$HOME/.local/bin/pikaraoke")
    SHARE_DIR="$HOME/.local/share/pikaraoke"
    mkdir -p "$SHARE_DIR"
    ICON_PATH="$SHARE_DIR/logo.icns"
    ICON_URL="https://raw.githubusercontent.com/vicwomg/pikaraoke/refs/heads/master/pikaraoke/static/icons/logo.icns"
    if [ ! -f "$ICON_PATH" ]; then
        curl -fsSL "$ICON_URL" -o "$ICON_PATH" || echo "Warning: Could not download icon"
    fi

    if [ "$OS_TYPE" == "Darwin" ]; then
        # macOS Shortcut creation
        create_macos_app() {
            local app_name="$1"
            local args="$2"
            local app_path="$HOME/Desktop/$app_name.app"

            # Create a simple app wrapper using AppleScript that launches in Terminal
            # We use 'do script' to ensure it runs in a shell with user paths (ffmpeg, etc)
            osacompile -o "$app_path" -e "tell application \"Terminal\"
                activate
                do script \"$PIKARAOKE_BIN $args\"
            end tell"

            if [ -f "$ICON_PATH" ]; then
                # Set the icon for the app
                osascript <<EOF
use framework "Foundation"
use framework "AppKit"

set filePath to POSIX path of "$app_path"
set imagePath to POSIX path of "$ICON_PATH"

set theImage to (current application's NSImage's alloc()'s initWithContentsOfFile:imagePath)
(current application's NSWorkspace's sharedWorkspace()'s setIcon:theImage forFile:filePath options:0)
EOF
            fi
        }

        create_macos_app "PiKaraoke" ""
        create_macos_app "PiKaraoke (headless)" "--headless"
        echo "macOS shortcuts created on Desktop."

    elif [ "$OS_TYPE" == "Linux" ]; then
        # Linux Shortcut creation
        create_linux_desktop() {
            local name="$1"
            local args="$2"
            local filename="$3"
            local target="$HOME/Desktop/$filename"

            cat <<EOF > "$target"
[Desktop Entry]
Version=1.0
Type=Application
Name=$name
Exec=$PIKARAOKE_BIN $args
Icon=$ICON_PATH
Terminal=true
Categories=AudioVideo;Player;
EOF
            chmod +x "$target"
        }

        if [ -d "$HOME/Desktop" ]; then
            create_linux_desktop "PiKaraoke" "" "PiKaraoke.desktop"
            create_linux_desktop "PiKaraoke (headless)" "--headless" "PiKaraoke-headless.desktop"
            echo "Linux shortcuts created on Desktop."
        else
            echo "Warning: Desktop directory not found. Skipping shortcut creation."
        fi
    fi
fi

echo ""
echo "--------------------------------------------------------"
echo "Installation complete!"
echo "Please restart your terminal or run 'source ~/.bashrc' (or ~/.zshrc) for PATH changes to take effect."
echo "Then, simply run: pikaraoke"
if [ $INSTALL_SHORTCUTS -eq 1 ]; then
    echo "Or use the shortcuts created on the Desktop."
fi
echo "--------------------------------------------------------"

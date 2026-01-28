#!/bin/bash

# PiKaraoke One-Line Installer
# Supports macOS (Homebrew) and Linux (apt-get)

set -e

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
INSTALL_LIST="pikaraoke (via pipx)"
SKIP_DENO=0
if command -v node &> /dev/null; then
    echo "Node.js detected. Skipping Deno installation."
    SKIP_DENO=1
fi

if [ "$OS_TYPE" == "Darwin" ]; then
    INSTALL_LIST="ffmpeg, pipx, $INSTALL_LIST"
    if [ $SKIP_DENO -eq 0 ]; then INSTALL_LIST="deno, $INSTALL_LIST"; fi
    if ! is_python_compatible; then INSTALL_LIST="python, $INSTALL_LIST"; fi
elif [ "$OS_TYPE" == "Linux" ]; then
    INSTALL_LIST="ffmpeg, pipx, $INSTALL_LIST"
    if ! is_python_compatible; then INSTALL_LIST="python3, $INSTALL_LIST"; fi
    if [ $SKIP_DENO -eq 0 ] && ! command -v deno &> /dev/null; then INSTALL_LIST="deno, $INSTALL_LIST"; fi
fi

echo "The following packages will be installed/updated: $INSTALL_LIST"
read -p "Do you want to proceed? (y/n) " -r < /dev/tty
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Installation cancelled."
    exit 1
fi

read -p "Do you want to install desktop shortcuts? (y/n) " -r < /dev/tty
INSTALL_SHORTCUTS=0
if [[ $REPLY =~ ^[Yy]$ ]]; then
    INSTALL_SHORTCUTS=1
fi
echo

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

    if is_python_compatible; then
        echo "Compatible Python version found. Skipping Python installation."
        if [ $SKIP_DENO -eq 1 ]; then
            brew install ffmpeg-full pipx
        else
            brew install ffmpeg-full deno pipx
        fi
    else
        echo "Python 3.10+ not found. Installing via Homebrew..."
        if [ $SKIP_DENO -eq 1 ]; then
            brew install ffmpeg-full pipx python
        else
            brew install ffmpeg-full deno pipx python
        fi
    fi

    # link ffmpeg-full to path since it is keg-only
    brew link ffmpeg-full

elif [ "$OS_TYPE" == "Linux" ]; then
    # Linux (Assumes Debian/Ubuntu/Raspberry Pi OS)
    if ! command -v apt-get &> /dev/null; then
        echo "Error: This script currently only supports Debian-based Linux distributions (using apt-get)."
        exit 1
    fi

    echo "Updating package lists..."
    sudo apt-get update

    if is_python_compatible; then
        echo "Compatible Python version found. Skipping Python installation."
        sudo apt-get install -y ffmpeg pipx
    else
        echo "Python 3.10+ not found. Installing via apt..."
        sudo apt-get install -y ffmpeg pipx python3
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

# Ensure pipx is in PATH
echo "Configuring pipx..."
pipx ensurepath
export PATH="$PATH:$HOME/.local/bin"

# Install pikaraoke
echo "Installing pikaraoke via pipx..."
if pipx list | grep -q "pikaraoke"; then
    echo "PiKaraoke is already installed. Upgrading..."
    pipx upgrade pikaraoke
else
    pipx install pikaraoke
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

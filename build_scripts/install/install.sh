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
INSTALL_LIST="pikaraoke (via pipx), yt-dlp (via pipx)"
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
read -p "Do you want to proceed? (y/n) " -n 1 -r < /dev/tty
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Installation cancelled."
    exit 1
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

    if is_python_compatible; then
        echo "Compatible Python version found. Skipping Python installation."
        if [ $SKIP_DENO -eq 1 ]; then
            brew install ffmpeg pipx
        else
            brew install ffmpeg deno pipx
        fi
    else
        echo "Python 3.10+ not found. Installing via Homebrew..."
        if [ $SKIP_DENO -eq 1 ]; then
            brew install ffmpeg pipx python
        else
            brew install ffmpeg deno pipx python
        fi
    fi

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

# Install dependencies via pipx
echo "Installing yt-dlp via pipx..."
if pipx list | grep -q "yt-dlp"; then
    echo "yt-dlp is already installed. Upgrading..."
    pipx upgrade yt-dlp
else
    pipx install yt-dlp
fi

# Install pikaraoke
echo "Installing pikaraoke via pipx..."
if pipx list | grep -q "pikaraoke"; then
    echo "PiKaraoke is already installed. Upgrading..."
    pipx upgrade pikaraoke
else
    pipx install pikaraoke
fi

echo ""
echo "--------------------------------------------------------"
echo "Installation complete!"
echo "You may need to restart your terminal or run 'source ~/.bashrc' (or ~/.zshrc) for PATH changes to take effect."
echo "Then, simply run: pikaraoke"
echo "--------------------------------------------------------"

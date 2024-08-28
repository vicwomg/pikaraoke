import subprocess


def run_command(command):
    result = subprocess.run(command, shell=True, text=True)
    if result.returncode != 0:
        raise Exception(f"ERROR: '{command}' failed with error code: {result.returncode}")


# Create an alias function for apt
def apt(command):
    run_command(f"sudo apt-get {command}")


def main():
    print("*** INSTALLING REQUIRED BINARIES ***")
    apt("update --allow-releaseinfo-change")
    apt("install ffmpeg -y")
    apt("install chromium-browser -y")
    apt("install chromium-chromedriver -y")


if __name__ == "__main__":
    main()

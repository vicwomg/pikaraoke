import logging
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import requests
from PIL import Image, ImageChops
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5555"


@pytest.fixture(scope="session", autouse=True)
def server():
    # Start the server before any tests run
    print("Starting server...")
    process = subprocess.Popen(
        ["pikaraoke", "--headless"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    # Wait for the server to start up
    # time.sleep(3)  # Adjust based on how long your server takes to start

    yield

    # Stop the server after all tests are done
    print("Stopping server...")
    process.terminate()
    process.wait()


@pytest.fixture
def enter_page(page: Page):
    """Opens the main screen and clicks the confirm button"""
    page.goto(f"{BASE_URL}/splash")
    page.get_by_role("button", name="Confirm").click()


@pytest.fixture(scope="session", autouse=True)
def configure_logging():
    # Generate filename with current date and time
    logs_folder = Path("logs-test") / "e2e"
    log_filename = logs_folder / datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log")
    logs_folder.mkdir(parents=True, exist_ok=True)  # Create logs/ folder

    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.DEBUG,  # Remember to move args before settup logging and use args here
        handlers=[logging.FileHandler(log_filename), logging.StreamHandler()],
    )


def images_equal(img1_path: Path, img2_path: Path) -> bool:
    """Compare two images and return True if they are identical."""
    img1 = Image.open(img1_path)
    img2 = Image.open(img2_path)

    if img1.mode != img2.mode or img1.size != img2.size:
        return False

    diff = ImageChops.difference(img1, img2)
    return diff.getbbox() is None


def download_image(url: str, save_path: Path):
    print(f"Downloading image: {url=} {save_path.name=}")
    response = requests.get(url)
    print(f"Response from download request: {response.status_code}")
    if response.status_code == 200:
        save_path.write_bytes(response.content)
        print(f"Saved {save_path.name} successfully.")

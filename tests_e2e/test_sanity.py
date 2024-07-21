from pathlib import Path

import pytest
from conftest import BASE_URL, download_image, images_equal
from PIL import Image
from playwright.sync_api import Page, expect
from pyzbar.pyzbar import decode


@pytest.mark.usefixtures("enter_page")
def test_logo_visible(page: Page, tmp_path: Path):
    """Verify that the logo splash screen is shown

    Checks that the logo object is visible. Also downloads the logo and verifies that it is
    in fact shown and matches the expected pikaraoke logo.
    """
    locator = page.get_by_role("img", name="logo")
    expect(locator).to_be_visible()

    # Download logo
    save_logo_path = tmp_path / "downloaded_logo.png"
    src = locator.get_attribute("src")
    download_image(f"{BASE_URL}{src}", save_logo_path)

    expected_logo_path = Path(__file__).parent / "resources/logo.png"
    assert images_equal(save_logo_path, expected_logo_path), "Logo does not match expected image."


@pytest.mark.skip(reason="Unable to parse the QR code.")
@pytest.mark.usefixtures("enter_page")
def test_qr_code_visible(page: Page, tmp_path: Path):
    """Verify QR code is shown

    Checks that QR code object is visible.
    Check that there is an IP address shown as a test under QR code.
    Downloads the QR code and reads it. Verifies that it matches the text of the IP address.
    """
    locator_qr_code = page.get_by_role("img", name="qrcode")

    # Download QR code
    qrcode_downloaded = tmp_path / "downloaded_qrcode.png"
    src = locator_qr_code.get_attribute("src")
    download_image(f"{BASE_URL}{src}", qrcode_downloaded)
    download_image(f"{BASE_URL}{src}", Path("qrcode.png"))

    # Load the image
    qrcode = Image.open(qrcode_downloaded)

    # Decode the QR code
    # decoded_objects = decode(qrcode)
    decoded_objects = decode(Image.open(Path("qrcode.png")))

    for obj in decoded_objects:
        print("Type:", obj.type)
        print("Data:", obj.data.decode("utf-8"))

    if not decoded_objects:
        print("No QR code found.")

    # locator_server_ip = page.locator("#qr-code").get_by_text("http://192.168.50.152:")

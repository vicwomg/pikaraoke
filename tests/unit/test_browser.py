"""Tests for the splash-screen browser launcher."""

from types import SimpleNamespace

from pikaraoke.lib.browser import Browser


def _fake_karaoke(**overrides) -> SimpleNamespace:
    defaults = {
        "url": "http://192.168.1.50:5555",
        "port": 5555,
        "is_raspberry_pi": False,
        "log_level": 20,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_splash_url_uses_loopback_not_the_advertised_url():
    browser = Browser(_fake_karaoke())
    assert browser.splash_url == "http://127.0.0.1:5555/splash"


def test_splash_url_ignores_url_override_and_base_path():
    karaoke = _fake_karaoke(url="https://karaoke.example.com/karaoke", port=5555)
    browser = Browser(karaoke)
    assert browser.splash_url == "http://127.0.0.1:5555/splash"


def test_splash_url_follows_the_configured_port():
    browser = Browser(_fake_karaoke(url="http://192.168.1.50:8080", port=8080))
    assert browser.splash_url == "http://127.0.0.1:8080/splash"

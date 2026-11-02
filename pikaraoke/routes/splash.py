"""Splash screen / player display route."""

import flask_babel
from flask import jsonify, render_template, url_for
from flask_smorest import Blueprint

from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.auth import public
from pikaraoke.lib.background_video import video_choices
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name

_ = flask_babel.gettext


splash_bp = Blueprint("splash", __name__)


def _default_score_phrases() -> dict[str, list[str]]:
    """Translated built-in phrases, used when the user has not set custom ones."""
    return {
        "low": [
            _("Never sing again... ever."),
            _("That was a really good impression of a dying cat!"),
            _("Thank God it's over."),
            _("Pass the mic, please!"),
            _("Well, I'm sure you're very good at your day job."),
        ],
        "mid": [
            _("I've seen better."),
            _("Ok... just ok."),
            _("Not bad for an amateur."),
            _("You put on a decent show."),
            _("That was... something."),
        ],
        "high": [
            _("Congratulations! That was unbelievable!"),
            _("Wow, have you tried auditioning for The Voice?"),
            _("Please, sing another one!"),
            _("You rock! You know that?!"),
            _("Woah, who let Freddie Mercury in here?"),
        ],
    }


def _parse_stored_phrases(stored: str) -> list[str]:
    """Split a stored phrase string on '|' (preferred) or '\\n' (legacy)."""
    sep = "|" if "|" in stored else "\n"
    return [p.strip() for p in stored.split(sep) if p.strip()]


def _get_active_score_phrases(k: Karaoke) -> dict[str, list[str]]:
    """Custom phrases if configured; translated built-in defaults otherwise."""
    defaults = _default_score_phrases()
    result = {}
    for tier in ("low", "mid", "high"):
        stored = getattr(k, f"{tier}_score_phrases")
        result[tier] = (_parse_stored_phrases(stored) if stored else []) or defaults[tier]
    return result


@splash_bp.route("/api/splash/score_phrases")
@public
def get_score_phrases():
    """Active score phrases as JSON — translated defaults or user-defined custom phrases."""
    return jsonify(_get_active_score_phrases(get_karaoke_instance()))


@splash_bp.route("/splash")
@public
def splash():
    """Splash screen / player display for TV output."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    network = k.network_info

    bg_videos = video_choices(k.bg_video_path)

    return render_template(
        "splash.html",
        site_title=site_name,
        blank_page=True,
        url=k.url,
        hotspot=network.get("hotspot", False),
        hotspot_name=network.get("hotspot_name", ""),
        hotspot_password=network.get("hotspot_password", ""),
        hide_url=k.hide_url,
        hide_qr_code=k.hide_qr_code,
        hide_session_name=k.hide_session_name,
        hide_logo=k.hide_logo,
        show_splash_clock=k.show_splash_clock,
        hide_overlay=k.hide_overlay,
        screensaver_timeout=k.screensaver_timeout,
        splash_scale=k.splash_scale,
        disable_bg_music=k.disable_bg_music,
        disable_bg_video=k.disable_bg_video,
        disable_score=k.disable_score,
        bg_music_volume=k.bg_music_volume,
        bg_video_urls=[url_for("stream.stream_bg_video", file=name) for name in bg_videos],
    )

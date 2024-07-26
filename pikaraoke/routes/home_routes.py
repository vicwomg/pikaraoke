import subprocess
from pathlib import Path

from flask import Blueprint, current_app, render_template, request
from flask_paginate import Pagination, get_page_parameter

from pikaraoke import get_current_app, is_admin

home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def home() -> str:
    current_app = get_current_app()
    return render_template(
        "home.html",
        site_title=current_app.config["SITE_NAME"],
        title="Home",
        transpose_value=current_app.karaoke.now_playing_transpose,
        admin=is_admin(password=current_app.config["ADMIN_PASSWORD"]),
    )


@home_bp.route("/splash")
def splash():
    current_app = get_current_app()
    # Only do this on Raspberry Pis
    if current_app.platform.is_rpi():
        status = subprocess.run(["iwconfig", "wlan0"], stdout=subprocess.PIPE).stdout.decode(
            "utf-8"
        )
        text = ""
        if "Mode:Master" in status:
            # Wifi is setup as a Access Point
            ap_name = ""
            ap_password = ""

            config_file = Path("/etc/raspiwifi/raspiwifi.conf")
            if config_file.is_file():
                content = config_file.read_text()

                # Override the default values according to the configuration file.
                for line in content.splitlines():
                    line = line.split("#", 1)[0]
                    if "ssid_prefix=" in line:
                        ap_name = line.split("ssid_prefix=")[1].strip()
                    elif "wpa_key=" in line:
                        ap_password = line.split("wpa_key=")[1].strip()

            if len(ap_password) > 0:
                text = [
                    f"Wifi Network: {ap_name} Password: {ap_password}",
                    f"Configure Wifi: {current_app.karaoke.url.rpartition(':')[0]}",
                ]
            else:
                text = [
                    f"Wifi Network: {ap_name}",
                    f"Configure Wifi: {current_app.rpartition(':',1)[0]}",
                ]
        else:
            # You are connected to Wifi as a client
            text = ""
    else:
        # Not a Raspberry Pi
        text = ""

    return render_template(
        "splash.html",
        blank_page=True,
        url=current_app.karaoke.url,
        hostap_info=text,
        hide_url=current_app.karaoke.hide_url,
        hide_overlay=current_app.karaoke.hide_overlay,
        screensaver_timeout=current_app.karaoke.screensaver_timeout,
    )


@home_bp.route("/queue")
def queue() -> str:
    current_app = get_current_app()
    return render_template(
        "queue.html",
        queue=current_app.karaoke.queue,
        site_title=current_app.config["SITE_NAME"],
        title="Queue",
        admin=is_admin(password=current_app.config["ADMIN_PASSWORD"]),
    )


@home_bp.route("/browse", methods=["GET"])
def browse():
    current_app = get_current_app()
    search = False
    q = request.args.get("q")
    if q:
        search = True
    page = request.args.get(get_page_parameter(), type=int, default=1)

    available_songs = current_app.karaoke.available_songs

    letter = request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = current_app.karaoke.filename_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = current_app.karaoke.filename_from_path(song).lower()
                if f.startswith(letter.lower()):
                    result.append(song)
        available_songs = result

    if "sort" in request.args and request.args["sort"] == "date":
        songs = sorted(available_songs, key=lambda x: Path(x).stat().st_ctime)
        songs.reverse()
        sort_order = "Date"
    else:
        songs = available_songs
        sort_order = "Alphabetical"

    # Ensure songs is a list of strings
    songs = [str(song) for song in songs]

    results_per_page = 500
    pagination = Pagination(
        css_framework="bulma",
        page=page,
        total=len(songs),
        search=search,
        record_name="songs",
        per_page=results_per_page,
    )

    start_index = (page - 1) * (results_per_page - 1)

    return render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        site_title=current_app.config["SITE_NAME"],
        letter=letter,
        # MSG: Title of the files page.
        title="Browse",
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(current_app.config["ADMIN_PASSWORD"]),
    )


@home_bp.route("/search", methods=["GET"])
def search():
    current_app = get_current_app()

    if "search_string" in request.args:
        search_string = request.args["search_string"]
        if "non_karaoke" in request.args and request.args["non_karaoke"] == "true":
            search_results = current_app.karaoke.get_search_results(search_string)
        else:
            search_results = current_app.karaoke.get_karaoke_search_results(search_string)
    else:
        search_string = None
        search_results = None

    return render_template(
        "search.html",
        site_title=current_app.config["SITE_NAME"],
        title="Search",
        songs=current_app.karaoke.available_songs,
        search_results=search_results,
        search_string=search_string,
    )

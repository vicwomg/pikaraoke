import datetime
import hashlib
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cherrypy
import flask
import flask_babel
import psutil
from flask_babel import Babel
from flask_paginate import Pagination, get_page_parameter
from yt_dlp.version import __version__ as yt_dlp_version

from pikaraoke.constants import LANGUAGES, VERSION
from pikaraoke.karaoke import Karaoke
from pikaraoke.lib.get_platform import get_platform
from pikaraoke.lib.parse_args import parse_args

try:
    from urllib.parse import quote, unquote
except ImportError:
    from urllib import quote, unquote

import webbrowser

logger = logging.getLogger(__name__)

VOLUME = 0.85

_ = flask_babel.gettext

app = flask.Flask(__name__)
app.secret_key = secrets.token_bytes(24)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
app.config["JSON_SORT_KEYS"] = False
babel = Babel(app)
site_name = "PiKaraoke"
admin_password = None
platform_current = get_platform()


def filename_from_path(file_path: str, remove_youtube_id=True) -> str:
    rc = Path(file_path).name
    if remove_youtube_id:
        try:
            rc = rc.split("---")[0]  # removes youtube id if present
        except TypeError:
            # more fun python 3 hacks
            rc = rc.split("---".encode("utf-8", "ignore"))[0]

    return rc


def url_escape(filename: str):
    return quote(filename.encode("utf8"))


def hash_dict(d) -> str:
    return hashlib.md5(
        json.dumps(d, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
    ).hexdigest()


def is_admin() -> bool:
    if admin_password is None:
        return True

    if "admin" in flask.request.cookies:
        a = flask.request.cookies.get("admin")
        if a == admin_password:
            return True

    return False


@babel.localeselector
def get_locale() -> str | None:
    """Select the language to display the webpage in based on the Accept-Language header"""
    return flask.request.accept_languages.best_match(LANGUAGES.keys())


@app.route("/")
def home() -> str:
    return flask.render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        transpose_value=karaoke.now_playing_transpose,
        admin=is_admin(),
    )


@app.route("/auth", methods=["POST"])
def auth():
    d = flask.request.form.to_dict()
    pw = d["admin-password"]
    if pw == admin_password:
        resp = flask.make_response(flask.redirect("/"))
        expire_date = datetime.datetime.now()
        expire_date = expire_date + datetime.timedelta(days=90)
        resp.set_cookie("admin", admin_password, expires=expire_date)
        # MSG: Message shown after logging in as admin successfully
        flask.flash(_("Admin mode granted!"), "is-success")
    else:
        resp = flask.make_response(flask.redirect(flask.url_for("login")))
        # MSG: Message shown after failing to login as admin
        flask.flash(_("Incorrect admin password!"), "is-danger")

    return resp


@app.route("/login")
def login() -> str:
    return flask.render_template("login.html")


@app.route("/logout")
def logout():
    resp = flask.make_response(flask.redirect("/"))
    resp.set_cookie("admin", "")
    flask.flash("Logged out of admin mode!", "is-success")

    return resp


@app.route("/nowplaying")
def nowplaying() -> str:
    try:
        if len(karaoke.queue) >= 1:
            next_song = karaoke.queue[0]["title"]
            next_user = karaoke.queue[0]["user"]
        else:
            next_song = None
            next_user = None
        rc = {
            "now_playing": karaoke.now_playing,
            "now_playing_user": karaoke.now_playing_user,
            "now_playing_command": karaoke.now_playing_command,
            "up_next": next_song,
            "next_user": next_user,
            "now_playing_url": karaoke.now_playing_url,
            "is_paused": karaoke.is_paused,
            "transpose_value": karaoke.now_playing_transpose,
            "volume": karaoke.volume,
        }
        rc["hash"] = hash_dict(rc)  # used to detect changes in the now playing data
        return json.dumps(rc)
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""


# Call this after receiving a command in the front end
@app.route("/clear_command")
def clear_command():
    karaoke.now_playing_command = None
    return ""


@app.route("/queue")
def queue() -> str:
    return flask.render_template(
        "queue.html",
        queue=karaoke.queue,
        site_title=site_name,
        title="Queue",
        admin=is_admin(),
    )


@app.route("/get_queue")
def get_queue() -> str:
    return json.dumps(karaoke.queue if len(karaoke.queue) >= 1 else [])


@app.route("/queue/addrandom", methods=["GET"])
def add_random():
    amount = int(flask.request.args["amount"])
    rc = karaoke.queue_add_random(amount)
    if rc:
        flask.flash("Added %s random tracks" % amount, "is-success")
    else:
        flask.flash("Ran out of songs!", "is-warning")

    return flask.redirect(flask.url_for("queue"))


@app.route("/queue/edit", methods=["GET"])
def queue_edit():
    action = flask.request.args["action"]
    if action == "clear":
        karaoke.queue_clear()
        flask.flash("Cleared the queue!", "is-warning")
        return flask.redirect(flask.url_for("queue"))

    song = unquote(flask.request.args["song"])
    if action == "down":
        if karaoke.queue_edit(song, "down"):
            flask.flash("Moved down in queue: " + song, "is-success")
        else:
            flask.flash("Error moving down in queue: " + song, "is-danger")
    elif action == "up":
        if karaoke.queue_edit(song, "up"):
            flask.flash("Moved up in queue: " + song, "is-success")
        else:
            flask.flash("Error moving up in queue: " + song, "is-danger")
    elif action == "delete":
        if karaoke.queue_edit(song, "delete"):
            flask.flash("Deleted from queue: " + song, "is-success")
        else:
            flask.flash("Error deleting from queue: " + song, "is-danger")

    return flask.redirect(flask.url_for("queue"))


@app.route("/enqueue", methods=["POST", "GET"])
def enqueue():
    if "song" in flask.request.args:
        song = flask.request.args["song"]
    else:
        d = flask.request.form.to_dict()
        song = d["song-to-add"]
    if "user" in flask.request.args:
        user = flask.request.args["user"]
    else:
        d = flask.request.form.to_dict()
        user = d["song-added-by"]
    rc = karaoke.enqueue(song, user)
    song_title = filename_from_path(song)

    return json.dumps({"song": song_title, "success": rc})


@app.route("/skip")
def skip():
    karaoke.skip()
    return flask.redirect(flask.url_for("home"))


@app.route("/pause")
def pause():
    karaoke.pause()
    return flask.redirect(flask.url_for("home"))


@app.route("/transpose/<semitones>", methods=["GET"])
def transpose(semitones):
    karaoke.transpose_current(int(semitones))
    return flask.redirect(flask.url_for("home"))


@app.route("/restart")
def restart():
    karaoke.restart()
    return flask.redirect(flask.url_for("home"))


@app.route("/volume/<volume>")
def volume(volume):
    karaoke.volume_change(float(volume))
    return flask.redirect(flask.url_for("home"))


@app.route("/vol_up")
def vol_up():
    karaoke.vol_up()
    return flask.redirect(flask.url_for("home"))


@app.route("/vol_down")
def vol_down():
    karaoke.vol_down()
    return flask.redirect(flask.url_for("home"))


@app.route("/search", methods=["GET"])
def search():
    if "search_string" in flask.request.args:
        search_string = flask.request.args["search_string"]
        if "non_karaoke" in flask.request.args and flask.request.args["non_karaoke"] == "true":
            search_results = karaoke.get_search_results(search_string)
        else:
            search_results = karaoke.get_karaoke_search_results(search_string)
    else:
        search_string = None
        search_results = None

    return flask.render_template(
        "search.html",
        site_title=site_name,
        title="Search",
        songs=karaoke.available_songs,
        search_results=search_results,
        search_string=search_string,
    )


@app.route("/autocomplete")
def autocomplete():
    q = flask.request.args.get("q").lower()
    result = []
    for each in karaoke.available_songs:
        if q in each.lower():
            result.append(
                {
                    "path": each,
                    "fileName": karaoke.filename_from_path(each),
                    "type": "autocomplete",
                }
            )
    response = app.response_class(response=json.dumps(result), mimetype="application/json")
    return response


@app.route("/browse", methods=["GET"])
def browse():
    search = False
    q = flask.request.args.get("q")
    if q:
        search = True
    page = flask.request.args.get(get_page_parameter(), type=int, default=1)

    available_songs = karaoke.available_songs

    letter = flask.request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = karaoke.filename_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = karaoke.filename_from_path(song).lower()
                if f.startswith(letter.lower()):
                    result.append(song)
        available_songs = result

    if "sort" in flask.request.args and flask.request.args["sort"] == "date":
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
    return flask.render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        site_title=site_name,
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(),
    )


@app.route("/download", methods=["POST"])
def download():
    d = flask.request.form.to_dict()
    song = d["song-url"]
    user = d["song-added-by"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=karaoke.download_video, args=[song, queue, user])
    t.daemon = True
    t.start()

    flash_message = (
        "Download started: '" + song + "'. This may take a couple of minutes to complete. "
    )

    if queue:
        flash_message += "Song will be added to queue."
    else:
        flash_message += 'Song will appear in the "available songs" list.'
    flask.flash(flash_message, "is-info")
    return flask.redirect(flask.url_for("search"))


@app.route("/qrcode")
def qrcode():
    return flask.send_file(karaoke.qr_code_path, mimetype="image/png")


@app.route("/logo")
def logo():
    return flask.send_file(karaoke.logo_path, mimetype="image/png")


@app.route("/end_song", methods=["GET"])
def end_song():
    karaoke.end_song()
    return "ok"


@app.route("/start_song", methods=["GET"])
def start_song():
    karaoke.start_song()
    return "ok"


@app.route("/files/delete", methods=["GET"])
def delete_file():
    if "song" in flask.request.args:
        song_path = flask.request.args["song"]
        if song_path in karaoke.queue:
            flask.flash(
                "Error: Can't delete this song because it is in the current queue: " + song_path,
                "is-danger",
            )
        else:
            karaoke.delete(Path(song_path))
            flask.flash("Song deleted: " + song_path, "is-warning")
    else:
        flask.flash("Error: No song parameter specified!", "is-danger")

    return flask.redirect(flask.url_for("browse"))


@app.route("/files/edit", methods=["GET", "POST"])
def edit_file():
    queue_error_msg = "Error: Can't edit this song because it is in the current queue: "
    if "song" in flask.request.args:
        song_path = flask.request.args["song"]
        # print "SONG_PATH" + song_path
        if song_path in karaoke.queue:
            flask.flash(queue_error_msg + song_path, "is-danger")
            return flask.redirect(flask.url_for("browse"))

        return flask.render_template(
            "edit.html",
            site_title=site_name,
            title="Song File Edit",
            song=song_path.encode("utf-8", "ignore"),
        )

    d = flask.request.form.to_dict()
    if "new_file_name" in d and "old_file_name" in d:
        new_name = d["new_file_name"]
        old_name = d["old_file_name"]
        if karaoke.is_song_in_queue(old_name):
            # check one more time just in case someone added it during editing
            flask.flash(queue_error_msg + song_path, "is-danger")
        else:
            # check if new_name already exist
            file_extension = Path(old_name).suffix
            new_file_path = (
                Path(karaoke.download_path).joinpath(new_name).with_suffix(file_extension)
            )
            if new_file_path.is_file():
                flask.flash(
                    "Error Renaming file: '%s' to '%s'. Filename already exists."
                    % (old_name, new_name + file_extension),
                    "is-danger",
                )
            else:
                karaoke.rename(old_name, new_name)
                flask.flash(
                    "Renamed file: '%s' to '%s'." % (old_name, new_name),
                    "is-warning",
                )
    else:
        flask.flash("Error: No filename parameters were specified!", "is-danger")
    return flask.redirect(flask.url_for("browse"))


@app.route("/splash")
def splash():
    # Only do this on Raspberry Pis
    if platform_current.is_rpi():
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
                    f"Configure Wifi: {karaoke.url.rpartition(':')[0]}",
                ]
            else:
                text = [
                    f"Wifi Network: {ap_name}",
                    f"Configure Wifi: {karaoke.url.rpartition(':',1)[0]}",
                ]
        else:
            # You are connected to Wifi as a client
            text = ""
    else:
        # Not a Raspberry Pi
        text = ""

    return flask.render_template(
        "splash.html",
        blank_page=True,
        url=karaoke.url,
        hostap_info=text,
        hide_url=karaoke.hide_url,
        hide_overlay=karaoke.hide_overlay,
        screensaver_timeout=karaoke.screensaver_timeout,
    )


@app.route("/info")
def info():
    url = karaoke.url

    # cpu
    cpu = str(psutil.cpu_percent()) + "%"

    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available / 1024.0 / 1024.0, 1)
    total = round(memory.total / 1024.0 / 1024.0, 1)
    memory = (
        str(available) + "MB free / " + str(total) + "MB total ( " + str(memory.percent) + "% )"
    )

    # disk
    disk = psutil.disk_usage("/")
    # Divide from Bytes -> KB -> MB -> GB
    free = round(disk.free / 1024.0 / 1024.0 / 1024.0, 1)
    total = round(disk.total / 1024.0 / 1024.0 / 1024.0, 1)
    disk = str(free) + "GB free / " + str(total) + "GB total ( " + str(disk.percent) + "% )"

    return flask.render_template(
        "info.html",
        site_title=site_name,
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        youtubedl_version=yt_dlp_version,
        is_pi=platform_current.is_rpi(),
        pikaraoke_version=VERSION,
        admin=is_admin(),
        admin_enabled=admin_password != None,
    )


# Delay system commands to allow redirect to render first
def delayed_halt(cmd):
    time.sleep(1.5)
    karaoke.queue_clear()
    cherrypy.engine.stop()
    cherrypy.engine.exit()
    karaoke.stop()
    if cmd == 0:
        sys.exit()
    if cmd == 1:
        os.system("shutdown now")
    if cmd == 2:
        os.system("reboot")
    if cmd == 3:
        process = subprocess.Popen(["raspi-config", "--expand-rootfs"])
        process.wait()
        os.system("reboot")


@app.route("/update_ytdl")
def update_ytdl():
    flask.flash("Support for updating ytdl removed.", "is-danger")


@app.route("/refresh")
def refresh():
    if is_admin():
        karaoke.get_available_songs()
    else:
        flask.flash("You don't have permission to shut down", "is-danger")
    return flask.redirect(flask.url_for("browse"))


@app.route("/quit")
def quit():
    if is_admin():
        flask.flash("Quitting pikaraoke now!", "is-warning")
        th = threading.Thread(target=delayed_halt, args=[0])
        th.start()
    else:
        flask.flash("You don't have permission to quit", "is-danger")
    return flask.redirect(flask.url_for("home"))


@app.route("/shutdown")
def shutdown():
    if is_admin():
        flask.flash("Shutting down system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[1])
        th.start()
    else:
        flask.flash("You don't have permission to shut down", "is-danger")
    return flask.redirect(flask.url_for("home"))


@app.route("/reboot")
def reboot():
    if is_admin():
        flask.flash("Rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[2])
        th.start()
    else:
        flask.flash("You don't have permission to Reboot", "is-danger")
    return flask.redirect(flask.url_for("home"))


@app.route("/expand_fs")
def expand_fs():
    if is_admin() and platform_current.is_rpi():
        flask.flash("Expanding filesystem and rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[3])
        th.start()
    elif not platform_current.is_rpi():
        flask.flash("Cannot expand fs on non-raspberry pi devices!", "is-danger")
    else:
        flask.flash("You don't have permission to resize the filesystem", "is-danger")

    return flask.redirect(flask.url_for("home"))


# Handle sigterm, apparently cherrypy won't shut down without explicit handling
signal.signal(signal.SIGTERM, lambda signum, stack_frame: karaoke.stop())


def _configure_logger(log_level: int):
    # Generate filename with current date and time
    logs_folder = Path("logs")
    log_filename = logs_folder / datetime.now().strftime("%Y-%m-%d_%H-%M-%S.log")
    logs_folder.mkdir(exist_ok=True)  # Create logs/ folder

    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=log_level,  # Remember to move args before settup logging and use args here
        handlers=[logging.FileHandler(log_filename), logging.StreamHandler()],
    )


def main():
    _configure_logger(log_level=logging.DEBUG)

    args = parse_args()

    global admin_password
    if args.admin_password:
        admin_password = args.admin_password

    app.jinja_env.globals.update(filename_from_path=filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    # setup/create download directory if necessary
    dl_path: Path = (
        args.download_path.expanduser()
    )  # Is it necessary to expand user? I don't think so.
    dl_path.mkdir(parents=True, exist_ok=True)

    # Configure karaoke process
    global karaoke

    karaoke = Karaoke(
        port=args.port,
        ffmpeg_port=args.ffmpeg_port,
        download_path=dl_path,
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=args.volume,
        hide_url=args.hide_url,
        hide_raspiwifi_instructions=args.hide_raspiwifi_instructions,
        hide_splash_screen=args.hide_splash_screen,
        high_quality=args.high_quality,
        logo_path=str(args.logo_path),
        hide_overlay=args.hide_overlay,
        screensaver_timeout=args.screensaver_timeout,
        url=args.url,
        ffmpeg_url=args.ffmpeg_url,
        prefer_hostname=args.prefer_hostname,
    )

    # Start the CherryPy WSGI web server
    cherrypy.tree.graft(app, "/")
    # Set the configuration of the web server
    cherrypy.config.update(
        {
            "engine.autoreload.on": False,
            "log.screen": True,
            "server.socket_port": int(args.port),
            "server.socket_host": "0.0.0.0",
            "server.thread_pool": 100,
        }
    )
    cherrypy.engine.start()

    # Start the splash screen using selenium
    if not args.hide_splash_screen:
        url = f"http://{karaoke.get_ip()}:5555/splash"
        logger.debug(f"Opening in default browser at {url}")
        webbrowser.open(url)

    # Start the karaoke process
    with karaoke:
        karaoke.run()

    # Close running processes when done
    cherrypy.engine.exit()

    sys.exit()


if __name__ == "__main__":
    main()

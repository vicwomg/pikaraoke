import argparse
import datetime
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import cherrypy
import flask_babel
import psutil
from flask import (
    Flask,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_babel import Babel
from flask_paginate import Pagination, get_page_parameter
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pikaraoke import VERSION, karaoke
from pikaraoke.constants import LANGUAGES
from pikaraoke.lib.get_platform import get_platform, is_raspberry_pi

try:
    from urllib.parse import quote, unquote
except ImportError:
    from urllib import quote, unquote

_ = flask_babel.gettext


app = Flask(__name__)
app.secret_key = os.urandom(24)
app.jinja_env.add_extension("jinja2.ext.i18n")
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
app.config["JSON_SORT_KEYS"] = False
babel = Babel(app)
site_name = "PiKaraoke"
admin_password = None
raspberry_pi = is_raspberry_pi()
linux = get_platform() == "linux"


def filename_from_path(file_path, remove_youtube_id=True):
    rc = os.path.basename(file_path)
    rc = os.path.splitext(rc)[0]
    if remove_youtube_id:
        try:
            rc = rc.split("---")[0]  # removes youtube id if present
        except TypeError:
            # more fun python 3 hacks
            rc = rc.split("---".encode("utf-8", "ignore"))[0]
    return rc


def arg_path_parse(path):
    if type(path) == list:
        return " ".join(path)
    else:
        return path


def url_escape(filename):
    return quote(filename.encode("utf8"))


def hash_dict(d):
    return hashlib.md5(
        json.dumps(d, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
    ).hexdigest()


def is_admin():
    if admin_password == None:
        return True
    if "admin" in request.cookies:
        a = request.cookies.get("admin")
        if a == admin_password:
            return True
    return False


@babel.localeselector
def get_locale():
    """Select the language to display the webpage in based on the Accept-Language header"""
    return request.accept_languages.best_match(LANGUAGES.keys())


@app.route("/")
def home():
    return render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        transpose_value=k.now_playing_transpose,
        admin=is_admin(),
    )


@app.route("/auth", methods=["POST"])
def auth():
    d = request.form.to_dict()
    p = d["admin-password"]
    if p == admin_password:
        resp = make_response(redirect("/"))
        expire_date = datetime.datetime.now()
        expire_date = expire_date + datetime.timedelta(days=90)
        resp.set_cookie("admin", admin_password, expires=expire_date)
        # MSG: Message shown after logging in as admin successfully
        flash(_("Admin mode granted!"), "is-success")
    else:
        resp = make_response(redirect(url_for("login")))
        # MSG: Message shown after failing to login as admin
        flash(_("Incorrect admin password!"), "is-danger")
    return resp


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/logout")
def logout():
    resp = make_response(redirect("/"))
    resp.set_cookie("admin", "")
    flash("Logged out of admin mode!", "is-success")
    return resp


@app.route("/nowplaying")
def nowplaying():
    try:
        if len(k.queue) >= 1:
            next_song = k.queue[0]["title"]
            next_user = k.queue[0]["user"]
        else:
            next_song = None
            next_user = None
        rc = {
            "now_playing": k.now_playing,
            "now_playing_user": k.now_playing_user,
            "now_playing_command": k.now_playing_command,
            "up_next": next_song,
            "next_user": next_user,
            "now_playing_url": k.now_playing_url,
            "is_paused": k.is_paused,
            "transpose_value": k.now_playing_transpose,
            "volume": k.volume,
        }
        rc["hash"] = hash_dict(rc)  # used to detect changes in the now playing data
        return json.dumps(rc)
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""


# Call this after receiving a command in the front end
@app.route("/clear_command")
def clear_command():
    k.now_playing_command = None
    return ""


@app.route("/queue")
def queue():
    return render_template(
        "queue.html", queue=k.queue, site_title=site_name, title="Queue", admin=is_admin()
    )


@app.route("/get_queue")
def get_queue():
    if len(k.queue) >= 1:
        return json.dumps(k.queue)
    else:
        return json.dumps([])


@app.route("/queue/addrandom", methods=["GET"])
def add_random():
    amount = int(request.args["amount"])
    rc = k.queue_add_random(amount)
    if rc:
        flash("Added %s random tracks" % amount, "is-success")
    else:
        flash("Ran out of songs!", "is-warning")
    return redirect(url_for("queue"))


@app.route("/queue/edit", methods=["GET"])
def queue_edit():
    action = request.args["action"]
    if action == "clear":
        k.queue_clear()
        flash("Cleared the queue!", "is-warning")
        return redirect(url_for("queue"))
    else:
        song = request.args["song"]
        song = unquote(song)
        if action == "down":
            result = k.queue_edit(song, "down")
            if result:
                flash("Moved down in queue: " + song, "is-success")
            else:
                flash("Error moving down in queue: " + song, "is-danger")
        elif action == "up":
            result = k.queue_edit(song, "up")
            if result:
                flash("Moved up in queue: " + song, "is-success")
            else:
                flash("Error moving up in queue: " + song, "is-danger")
        elif action == "delete":
            result = k.queue_edit(song, "delete")
            if result:
                flash("Deleted from queue: " + song, "is-success")
            else:
                flash("Error deleting from queue: " + song, "is-danger")
    return redirect(url_for("queue"))


@app.route("/enqueue", methods=["POST", "GET"])
def enqueue():
    if "song" in request.args:
        song = request.args["song"]
    else:
        d = request.form.to_dict()
        song = d["song-to-add"]
    if "user" in request.args:
        user = request.args["user"]
    else:
        d = request.form.to_dict()
        user = d["song-added-by"]
    rc = k.enqueue(song, user)
    song_title = filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})


@app.route("/skip")
def skip():
    k.skip()
    return redirect(url_for("home"))


@app.route("/pause")
def pause():
    k.pause()
    return redirect(url_for("home"))


@app.route("/transpose/<semitones>", methods=["GET"])
def transpose(semitones):
    k.transpose_current(int(semitones))
    return redirect(url_for("home"))


@app.route("/restart")
def restart():
    k.restart()
    return redirect(url_for("home"))


@app.route("/volume/<volume>")
def volume(volume):
    k.volume_change(float(volume))
    return redirect(url_for("home"))


@app.route("/vol_up")
def vol_up():
    k.vol_up()
    return redirect(url_for("home"))


@app.route("/vol_down")
def vol_down():
    k.vol_down()
    return redirect(url_for("home"))


@app.route("/search", methods=["GET"])
def search():
    if "search_string" in request.args:
        search_string = request.args["search_string"]
        if "non_karaoke" in request.args and request.args["non_karaoke"] == "true":
            search_results = k.get_search_results(search_string)
        else:
            search_results = k.get_karaoke_search_results(search_string)
    else:
        search_string = None
        search_results = None
    return render_template(
        "search.html",
        site_title=site_name,
        title="Search",
        songs=k.available_songs,
        search_results=search_results,
        search_string=search_string,
    )


@app.route("/autocomplete")
def autocomplete():
    q = request.args.get("q").lower()
    result = []
    for each in k.available_songs:
        if q in each.lower():
            result.append(
                {"path": each, "fileName": k.filename_from_path(each), "type": "autocomplete"}
            )
    response = app.response_class(response=json.dumps(result), mimetype="application/json")
    return response


@app.route("/browse", methods=["GET"])
def browse():
    search = False
    q = request.args.get("q")
    if q:
        search = True
    page = request.args.get(get_page_parameter(), type=int, default=1)

    available_songs = k.available_songs

    letter = request.args.get("letter")

    if letter:
        result = []
        if letter == "numeric":
            for song in available_songs:
                f = k.filename_from_path(song)[0]
                if f.isnumeric():
                    result.append(song)
        else:
            for song in available_songs:
                f = k.filename_from_path(song).lower()
                if f.startswith(letter.lower()):
                    result.append(song)
        available_songs = result

    if "sort" in request.args and request.args["sort"] == "date":
        songs = sorted(available_songs, key=lambda x: os.path.getctime(x))
        songs.reverse()
        sort_order = "Date"
    else:
        songs = available_songs
        sort_order = "Alphabetical"

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
        site_title=site_name,
        letter=letter,
        # MSG: Title of the files page.
        title=_("Browse"),
        songs=songs[start_index : start_index + results_per_page],
        admin=is_admin(),
    )


@app.route("/download", methods=["POST"])
def download():
    d = request.form.to_dict()
    song = d["song-url"]
    user = d["song-added-by"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=k.download_video, args=[song, queue, user])
    t.daemon = True
    t.start()

    flash_message = (
        "Download started: '" + song + "'. This may take a couple of minutes to complete. "
    )

    if queue:
        flash_message += "Song will be added to queue."
    else:
        flash_message += 'Song will appear in the "available songs" list.'
    flash(flash_message, "is-info")
    return redirect(url_for("search"))


@app.route("/qrcode")
def qrcode():
    return send_file(k.qr_code_path, mimetype="image/png")


@app.route("/logo")
def logo():
    return send_file(k.logo_path, mimetype="image/png")


@app.route("/end_song", methods=["GET"])
def end_song():
    k.end_song()
    return "ok"


@app.route("/start_song", methods=["GET"])
def start_song():
    k.start_song()
    return "ok"


@app.route("/files/delete", methods=["GET"])
def delete_file():
    if "song" in request.args:
        song_path = request.args["song"]
        if song_path in k.queue:
            flash(
                "Error: Can't delete this song because it is in the current queue: " + song_path,
                "is-danger",
            )
        else:
            k.delete(song_path)
            flash("Song deleted: " + song_path, "is-warning")
    else:
        flash("Error: No song parameter specified!", "is-danger")
    return redirect(url_for("browse"))


@app.route("/files/edit", methods=["GET", "POST"])
def edit_file():
    queue_error_msg = "Error: Can't edit this song because it is in the current queue: "
    if "song" in request.args:
        song_path = request.args["song"]
        # print "SONG_PATH" + song_path
        if song_path in k.queue:
            flash(queue_error_msg + song_path, "is-danger")
            return redirect(url_for("browse"))
        else:
            return render_template(
                "edit.html",
                site_title=site_name,
                title="Song File Edit",
                song=song_path.encode("utf-8", "ignore"),
            )
    else:
        d = request.form.to_dict()
        if "new_file_name" in d and "old_file_name" in d:
            new_name = d["new_file_name"]
            old_name = d["old_file_name"]
            if k.is_song_in_queue(old_name):
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + old_name, "is-danger")
            else:
                # check if new_name already exist
                file_extension = os.path.splitext(old_name)[1]
                if os.path.isfile(os.path.join(k.download_path, new_name + file_extension)):
                    flash(
                        "Error Renaming file: '%s' to '%s'. Filename already exists."
                        % (old_name, new_name + file_extension),
                        "is-danger",
                    )
                else:
                    k.rename(old_name, new_name)
                    flash(
                        "Renamed file: '%s' to '%s'." % (old_name, new_name),
                        "is-warning",
                    )
        else:
            flash("Error: No filename parameters were specified!", "is-danger")
        return redirect(url_for("browse"))


@app.route("/splash")
def splash():
    # Only do this on Raspberry Pis
    if raspberry_pi:
        status = subprocess.run(["iwconfig", "wlan0"], stdout=subprocess.PIPE).stdout.decode(
            "utf-8"
        )
        text = ""
        if "Mode:Master" in status:
            # Wifi is setup as a Access Point
            ap_name = ""
            ap_password = ""

            if os.path.isfile("/etc/raspiwifi/raspiwifi.conf"):
                f = open("/etc/raspiwifi/raspiwifi.conf", "r")

                # Override the default values according to the configuration file.
                for line in f.readlines():
                    line = line.split("#", 1)[0]
                    if "ssid_prefix=" in line:
                        ap_name = line.split("ssid_prefix=")[1].strip()
                    elif "wpa_key=" in line:
                        ap_password = line.split("wpa_key=")[1].strip()

            if len(ap_password) > 0:
                text = [
                    f"Wifi Network: {ap_name} Password: {ap_password}",
                    f"Configure Wifi: {k.url.rpartition(':')[0]}",
                ]
            else:
                text = [f"Wifi Network: {ap_name}", f"Configure Wifi: {k.url.rpartition(':',1)[0]}"]
        else:
            # You are connected to Wifi as a client
            text = ""
    else:
        # Not a Raspberry Pi
        text = ""

    return render_template(
        "splash.html",
        blank_page=True,
        url=k.url,
        hostap_info=text,
        hide_url=k.hide_url,
        hide_overlay=k.hide_overlay,
        screensaver_timeout=k.screensaver_timeout,
    )


@app.route("/info")
def info():
    url = k.url

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

    # youtube-dl
    youtubedl_version = k.youtubedl_version

    return render_template(
        "info.html",
        site_title=site_name,
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        ffmpeg_version=k.ffmpeg_version,
        youtubedl_version=youtubedl_version,
        platform=k.platform,
        os_version=k.os_version,
        is_pi=raspberry_pi,
        is_linux=linux,
        pikaraoke_version=VERSION,
        admin=is_admin(),
        admin_enabled=admin_password != None,
    )


# Delay system commands to allow redirect to render first
def delayed_halt(cmd):
    time.sleep(1.5)
    k.queue_clear()
    cherrypy.engine.stop()
    cherrypy.engine.exit()
    k.stop()
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


def update_youtube_dl():
    time.sleep(3)
    k.upgrade_youtubedl()


@app.route("/update_ytdl")
def update_ytdl():
    if is_admin():
        flash(
            "Updating youtube-dl! Should take a minute or two... ",
            "is-warning",
        )
        th = threading.Thread(target=update_youtube_dl)
        th.start()
    else:
        flash("You don't have permission to update youtube-dl", "is-danger")
    return redirect(url_for("home"))


@app.route("/refresh")
def refresh():
    if is_admin():
        k.get_available_songs()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("browse"))


@app.route("/quit")
def quit():
    if is_admin():
        flash("Quitting pikaraoke now!", "is-warning")
        th = threading.Thread(target=delayed_halt, args=[0])
        th.start()
    else:
        flash("You don't have permission to quit", "is-danger")
    return redirect(url_for("home"))


@app.route("/shutdown")
def shutdown():
    if is_admin():
        flash("Shutting down system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[1])
        th.start()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("home"))


@app.route("/reboot")
def reboot():
    if is_admin():
        flash("Rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[2])
        th.start()
    else:
        flash("You don't have permission to Reboot", "is-danger")
    return redirect(url_for("home"))


@app.route("/expand_fs")
def expand_fs():
    if is_admin() and raspberry_pi:
        flash("Expanding filesystem and rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[3])
        th.start()
    elif not raspberry_pi:
        flash("Cannot expand fs on non-raspberry pi devices!", "is-danger")
    else:
        flash("You don't have permission to resize the filesystem", "is-danger")
    return redirect(url_for("home"))


# Handle sigterm, apparently cherrypy won't shut down without explicit handling
signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())


def get_default_dl_dir(platform):
    if raspberry_pi:
        return "~/pikaraoke-songs"
    elif platform == "windows":
        legacy_directory = os.path.expanduser("~\\pikaraoke\\songs")
        if os.path.exists(legacy_directory):
            return legacy_directory
        else:
            return "~\\pikaraoke-songs"
    else:
        legacy_directory = "~/pikaraoke/songs"
        if os.path.exists(legacy_directory):
            return legacy_directory
        else:
            return "~/pikaraoke-songs"


def main():
    platform = get_platform()
    default_port = 5555
    default_ffmpeg_port = 5556
    default_volume = 0.85
    default_normalize_audio = False
    default_splash_delay = 3
    default_screensaver_delay = 300
    default_log_level = logging.INFO
    default_prefer_hostname = False

    default_dl_dir = get_default_dl_dir(platform)
    default_youtubedl_path = "yt-dlp"

    # parse CLI args
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-p",
        "--port",
        help="Desired http port (default: %d)" % default_port,
        default=default_port,
        required=False,
    )
    parser.add_argument(
        "--window-size",
        help="Desired window geometry in pixels, specified as width,height",
        default=0,
        required=False,
    )
    parser.add_argument(
        "-f",
        "--ffmpeg-port",
        help=f"Desired ffmpeg port. This is where video stream URLs will be pointed (default: {default_ffmpeg_port})",
        default=default_ffmpeg_port,
        required=False,
    )
    parser.add_argument(
        "-d",
        "--download-path",
        nargs="+",
        help="Desired path for downloaded songs. (default: %s)" % default_dl_dir,
        default=default_dl_dir,
        required=False,
    )
    parser.add_argument(
        "-y",
        "--youtubedl-path",
        nargs="+",
        help="Path of youtube-dl. (default: %s)" % default_youtubedl_path,
        default=default_youtubedl_path,
        required=False,
    )
    parser.add_argument(
        "-v",
        "--volume",
        help="Set initial player volume. A value between 0 and 1. (default: %s)" % default_volume,
        default=default_volume,
        required=False,
    )
    parser.add_argument(
        "-n",
        "--normalize-audio",
        help="Normalize volume. May cause performance issues on slower devices (default: %s)"
        % default_normalize_audio,
        action="store_true",
        default=default_normalize_audio,
        required=False,
    )
    parser.add_argument(
        "-s",
        "--splash-delay",
        help="Delay during splash screen between songs (in secs). (default: %s )"
        % default_splash_delay,
        default=default_splash_delay,
        required=False,
    )
    parser.add_argument(
        "-t",
        "--screensaver-timeout",
        help="Delay before the screensaver begins (in secs). (default: %s )"
        % default_screensaver_delay,
        default=default_screensaver_delay,
        required=False,
    )
    parser.add_argument(
        "-l",
        "--log-level",
        help=f"Logging level int value (DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50). (default: {default_log_level} )",
        default=default_log_level,
        required=False,
    )
    parser.add_argument(
        "--hide-url",
        action="store_true",
        help="Hide URL and QR code from the splash screen.",
        required=False,
    )
    parser.add_argument(
        "--prefer-hostname",
        action="store_true",
        help=f"Use the local hostname instead of the IP as the connection URL. Use at your discretion: mDNS is not guaranteed to work on all LAN configurations. Defaults to {default_prefer_hostname}",
        default=default_prefer_hostname,
        required=False,
    )
    parser.add_argument(
        "--hide-raspiwifi-instructions",
        action="store_true",
        help="Hide RaspiWiFi setup instructions from the splash screen.",
        required=False,
    )
    parser.add_argument(
        "--hide-splash-screen",
        "--headless",
        action="store_true",
        help="Headless mode. Don't launch the splash screen/player on the pikaraoke server",
        required=False,
    )
    parser.add_argument(
        "--high-quality",
        action="store_true",
        help="Download higher quality video. Note: requires ffmpeg and may cause CPU, download speed, and other performance issues",
        required=False,
    )
    parser.add_argument(
        "--logo-path",
        nargs="+",
        help="Path to a custom logo image file for the splash screen. Recommended dimensions ~ 2048x1024px",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "-u",
        "--url",
        help="Override the displayed IP address with a supplied URL. This argument should include port, if necessary",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "-m",
        "--ffmpeg-url",
        help="Override the ffmpeg address with a supplied URL.",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "--hide-overlay",
        action="store_true",
        help="Hide overlay that shows on top of video with pikaraoke QR code and IP",
        required=False,
    ),
    parser.add_argument(
        "--admin-password",
        help="Administrator password, for locking down certain features of the web UI such as queue editing, player controls, song editing, and system shutdown. If unspecified, everyone is an admin.",
        default=None,
        required=False,
    ),

    args = parser.parse_args()

    if args.admin_password:
        global admin_password
        admin_password = args.admin_password

    app.jinja_env.globals.update(filename_from_path=filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    # setup/create download directory if necessary
    dl_path = os.path.expanduser(arg_path_parse(args.download_path))
    if not dl_path.endswith("/"):
        dl_path += "/"
    if not os.path.exists(dl_path):
        print("Creating download path: " + dl_path)
        os.makedirs(dl_path)

    parsed_volume = float(args.volume)
    if parsed_volume > 1 or parsed_volume < 0:
        # logging.warning("Volume must be between 0 and 1. Setting to default: %s" % default_volume)
        print(
            f"[ERROR] Volume: {args.volume} must be between 0 and 1. Setting to default: {default_volume}"
        )
        parsed_volume = default_volume

    # Configure karaoke process
    global k
    k = karaoke.Karaoke(
        port=args.port,
        ffmpeg_port=args.ffmpeg_port,
        download_path=dl_path,
        youtubedl_path=arg_path_parse(args.youtubedl_path),
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=parsed_volume,
        normalize_audio=args.normalize_audio,
        hide_url=args.hide_url,
        hide_raspiwifi_instructions=args.hide_raspiwifi_instructions,
        hide_splash_screen=args.hide_splash_screen,
        high_quality=args.high_quality,
        logo_path=arg_path_parse(args.logo_path),
        hide_overlay=args.hide_overlay,
        screensaver_timeout=args.screensaver_timeout,
        url=args.url,
        ffmpeg_url=args.ffmpeg_url,
        prefer_hostname=args.prefer_hostname,
    )
    k.upgrade_youtubedl()

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
        if raspberry_pi:
            service = Service(executable_path="/usr/bin/chromedriver")
        else:
            service = None
        options = Options()

        if args.window_size:
            options.add_argument("--window-size=%s" % (args.window_size))
            options.add_argument("--window-position=0,0")

        options.add_argument("--kiosk")
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        try:
            driver = webdriver.Chrome(service=service, options=options)
            driver.get(f"{k.url}/splash")
            driver.add_cookie({"name": "user", "value": "PiKaraoke-Host"})
            # Clicking this counts as an interaction, which will allow the browser to autoplay audio
            wait = WebDriverWait(driver, 60)
            elem = wait.until(EC.element_to_be_clickable((By.ID, "permissions-button")))
            elem.click()
        except SessionNotCreatedException as e:
            print(str(e))
            print(
                f"\n[ERROR] Error starting splash screen. If you're running headed mode over SSH, you may need to run `export DISPLAY=:0.0` first to target the host machine's screen. Example: `export DISPLAY=:0.0; pikaraoke`\n"
            )
            cherrypy.engine.exit()
            sys.exit()

    # Start the karaoke process
    k.run()

    # Close running processes when done
    if not args.hide_splash_screen:
        driver.close()
    cherrypy.engine.exit()

    sys.exit()


if __name__ == "__main__":
    main()

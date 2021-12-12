import argparse
import datetime
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from functools import wraps

import cherrypy
import psutil
from flask import (Flask, flash, jsonify, make_response, redirect,
                   render_template, request, send_file, send_from_directory,
                   url_for)
from flask_paginate import Pagination, get_page_parameter

import karaoke
from constants import VERSION
from lib.get_platform import get_platform
from lib.vlcclient import get_default_vlc_path

try:
    from urllib.parse import quote, unquote
except ImportError:
    from urllib import quote, unquote


app = Flask(__name__)
app.secret_key = os.urandom(24)
site_name = "PiKaraoke"
admin_password = None

def filename_from_path(file_path, remove_youtube_id=True):
    rc = os.path.basename(file_path)
    rc = os.path.splitext(rc)[0]
    if remove_youtube_id:
        try:
            rc = rc.split("---")[0]  # removes youtube id if present
        except TypeError:
            # more fun python 3 hacks
            rc = rc.split("---".encode("utf-8"))[0]
    return rc


def url_escape(filename):
    return quote(filename.encode("utf8"))


def is_admin():
    if (admin_password == None):
        return True
    if ('admin' in request.cookies):
        a = request.cookies.get("admin")
        if (a == admin_password):
            return True
    return False


@app.route("/")
def home():
    return render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        show_transpose=k.use_vlc,
        transpose_value=k.now_playing_transpose,
        admin=is_admin()
    )

@app.route("/auth", methods=["POST"])
def auth():
    d = request.form.to_dict()
    p = d["admin-password"]
    if (p == admin_password):
        resp = make_response(redirect('/'))
        expire_date = datetime.datetime.now()
        expire_date = expire_date + datetime.timedelta(days=90)
        resp.set_cookie('admin', admin_password, expires=expire_date)
        flash("Admin mode granted!", "is-success")
    else:
        resp = make_response(redirect(url_for('login')))
        flash("Incorrect admin password!", "is-danger")
    return resp

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/logout")
def logout():
    resp = make_response(redirect('/'))
    resp.set_cookie('admin', '')
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
            "up_next": next_song,
            "next_user": next_user,
            "is_paused": k.is_paused,
            "transpose_value": k.now_playing_transpose,
        }
        return json.dumps(rc)
    except (Exception) as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
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
    # if rc:
    #     flash("Song added to queue: " + song_title, "is-success")
    # else:
    #     flash("Song is already in queue: " + song_title, "is-danger")
    #return redirect(url_for("home"))
    return json.dumps({"song": song_title, "success": rc })


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
    k.transpose_current(semitones)
    return redirect(url_for("home"))


@app.route("/restart")
def restart():
    k.restart()
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
        if ("non_karaoke" in request.args and request.args["non_karaoke"] == "true"):
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
    q = request.args.get('q').lower()
    result = []
    for each in k.available_songs:
        if q in each.lower():
            result.append({"path": each, "fileName": k.filename_from_path(each), "type": "autocomplete"})
    response = app.response_class(
        response=json.dumps(result),
        mimetype='application/json'
    )
    return response

@app.route("/browse", methods=["GET"])
def browse():
    search = False
    q = request.args.get('q')
    if q:
        search = True
    page = request.args.get(get_page_parameter(), type=int, default=1)

    available_songs = k.available_songs

    letter = request.args.get('letter')
   
    if (letter):
        result = []
        if (letter == "numeric"):
            for song in available_songs:
                f = k.filename_from_path(song)[0]
                if (f.isnumeric()):
                    result.append(song)
        else: 
            for song in available_songs:
                f = k.filename_from_path(song).lower()
                if (f.startswith(letter.lower())):
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
    pagination = Pagination(css_framework='bulma', page=page, total=len(songs), search=search, record_name='songs', per_page=results_per_page)
    start_index = (page - 1) * (results_per_page - 1)
    return render_template(
        "files.html",
        pagination=pagination,
        sort_order=sort_order,
        site_title=site_name,
        letter=letter,
        title="Browse",
        songs=songs[start_index:start_index + results_per_page],
        admin=is_admin()
    )


@app.route("/download", methods=["POST"])
def download():
    d = request.form.to_dict()
    song = d["song-url"]

    if "song-title" in d:
        songTitle = d["song-title"]
    else:
        songTitle = "Unknown"

    user = d["song-added-by"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=k.download_video, args=[songTitle, song, queue, user])
    t.daemon = True
    t.start()

    flash_message = (
        "Download started: '"
        + song
        + "'. This may take a couple of minutes to complete. "
    )

    if queue:
        flash_message += "Song will be added to queue."
    else:
        flash_message += 'Song will appear in the "available songs" list.'
    flash(flash_message, "is-info")
    return redirect(url_for("search"))


@app.route("/downloading", methods=["GET"])
def downloading():
    return render_template(
        "downloading.html", title="Downloading"
    )


@app.route("/download_progress", methods=["GET"])
def download_progress():
    response = app.response_class(
        response=json.dumps(k.get_download_progress()),
        mimetype='application/json'
    )
    return response

@app.route("/qrcode")
def qrcode():
    return send_file(k.qr_code_path, mimetype="image/png")


@app.route("/files/delete", methods=["GET"])
def delete_file():
    if "song" in request.args:
        song_path = request.args["song"]
        if song_path in k.queue:
            flash(
                "Error: Can't delete this song because it is in the current queue: "
                + song_path,
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
                song=song_path.encode("utf-8"),
            )
    else:
        d = request.form.to_dict()
        if "new_file_name" in d and "old_file_name" in d:
            new_name = d["new_file_name"]
            old_name = d["old_file_name"]
            if k.is_song_in_queue(old_name):
                # check one more time just in case someone added it during editing
                flash(queue_error_msg + song_path, "is-danger")
            else:
                # check if new_name already exist
                file_extension = os.path.splitext(old_name)[1]
                if os.path.isfile(
                    os.path.join(k.download_path, new_name + file_extension)
                ):
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


@app.route("/info")
def info():
    url = "http://" + request.host

    # cpu
    cpu = str(psutil.cpu_percent()) + "%"

    # mem
    memory = psutil.virtual_memory()
    available = round(memory.available / 1024.0 / 1024.0, 1)
    total = round(memory.total / 1024.0 / 1024.0, 1)
    memory = (
        str(available)
        + "MB free / "
        + str(total)
        + "MB total ( "
        + str(memory.percent)
        + "% )"
    )

    # disk
    disk = psutil.disk_usage("/")
    # Divide from Bytes -> KB -> MB -> GB
    free = round(disk.free / 1024.0 / 1024.0 / 1024.0, 1)
    total = round(disk.total / 1024.0 / 1024.0 / 1024.0, 1)
    disk = (
        str(free)
        + "GB free / "
        + str(total)
        + "GB total ( "
        + str(disk.percent)
        + "% )"
    )

    # youtube-dl
    youtubedl_version = k.youtubedl_version

    is_pi = get_platform() == "raspberry_pi"

    return render_template(
        "info.html",
        site_title=site_name,
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        youtubedl_version=youtubedl_version,
        is_pi=is_pi,
        pikaraoke_version=VERSION,
        admin=is_admin(),
        admin_enabled=admin_password != None
    )


# Delay system commands to allow redirect to render first
def delayed_halt(cmd):
    time.sleep(3)
    k.queue_clear()  # stop all pending omxplayer processes
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
    if (is_admin()):
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
    if (is_admin()):
        k.get_available_songs()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("browse"))

@app.route("/quit")
def quit():
    if (is_admin()):
        flash("Quitting pikaraoke now!", "is-warning")
        th = threading.Thread(target=delayed_halt, args=[0])
        th.start()
    else:
        flash("You don't have permission to quit", "is-danger")
    return redirect(url_for("home"))


@app.route("/shutdown")
def shutdown():
    if (is_admin()): 
        flash("Shutting down system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[1])
        th.start()
    else:
        flash("You don't have permission to shut down", "is-danger")
    return redirect(url_for("home"))


@app.route("/reboot")
def reboot():
    if (is_admin()): 
        flash("Rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[2])
        th.start()
    else:
        flash("You don't have permission to Reboot", "is-danger")
    return redirect(url_for("home"))

@app.route("/expand_fs")
def expand_fs():
    if (is_admin() and platform == "raspberry_pi"): 
        flash("Expanding filesystem and rebooting system now!", "is-danger")
        th = threading.Thread(target=delayed_halt, args=[3])
        th.start()
    elif (platform != "raspberry_pi"):
        flash("Cannot expand fs on non-raspberry pi devices!", "is-danger")
    else:
        flash("You don't have permission to resize the filesystem", "is-danger")
    return redirect(url_for("home"))


# Handle sigterm, apparently cherrypy won't shut down without explicit handling
signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())

def get_default_youtube_dl_path(platform):
    if platform == "windows":
        choco_ytdl_path = r"C:\ProgramData\chocolatey\bin\youtube-dl.exe"
        scoop_ytdl_path = os.path.expanduser(r"~\scoop\shims\youtube-dl.exe")
        if os.path.isfile(choco_ytdl_path):
            return choco_ytdl_path
        if os.path.isfile(scoop_ytdl_path):
            return scoop_ytdl_path
        return r"C:\Program Files\youtube-dl\youtube-dl.exe"
    else:
        return "/usr/local/bin/youtube-dl"

def get_default_dl_dir(platform):
    if platform == "raspberry_pi":
        return "/usr/lib/pikaraoke/songs"
    elif platform == "windows":
        legacy_directory = os.path.expanduser("~\pikaraoke\songs")
        if os.path.exists(legacy_directory):
            return legacy_directory
        else:
            return "~\pikaraoke-songs"
    else:
        legacy_directory = "~/pikaraoke/songs"
        if os.path.exists(legacy_directory):
            return legacy_directory
        else:
            return "~/pikaraoke-songs"


if __name__ == "__main__":

    platform = get_platform()
    default_port = 5000
    default_volume = 0
    default_splash_delay = 5
    default_log_level = logging.INFO

    default_dl_dir = get_default_dl_dir(platform)
    default_omxplayer_path = "/usr/bin/omxplayer"
    default_adev = "both"
    default_youtubedl_path = get_default_youtube_dl_path(platform)
    default_vlc_path = get_default_vlc_path(platform)
    default_vlc_port = 5002

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
        "-d",
        "--download-path",
        help="Desired path for downloaded songs. (default: %s)" % default_dl_dir,
        default=default_dl_dir,
        required=False,
    )
    parser.add_argument(
        "-o",
        "--omxplayer-path",
        help="Path of omxplayer. Only important to raspberry pi hardware. (default: %s)"
        % default_omxplayer_path,
        default=default_omxplayer_path,
        required=False,
    )
    parser.add_argument(
        "-y",
        "--youtubedl-path",
        help="Path of youtube-dl. (default: %s)" % default_youtubedl_path,
        default=default_youtubedl_path,
        required=False,
    )
    parser.add_argument(
        "-v",
        "--volume",
        help="If using omxplayer, the initial player volume is specified in millibels. Negative values ok. (default: %s , Note: 100 millibels = 1 decibel)."
        % default_volume,
        default=default_volume,
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
        "-l",
        "--log-level",
        help="Logging level int value (DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50). (default: %s )"
        % default_log_level,
        default=default_log_level,
        required=False,
    )
    parser.add_argument(
        "--hide-ip",
        action="store_true",
        help="Hide IP address from the screen.",
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
        action="store_true",
        help="Hide splash screen before/between songs.",
        required=False,
    )
    parser.add_argument(
        "--adev",
        help="Pass the audio output device argument to omxplayer. Possible values: hdmi/local/both/alsa[:device]. If you are using a rpi USB soundcard or Hifi audio hat, try: 'alsa:hw:0,0' Default: '%s'"
        % default_adev,
        default=default_adev,
        required=False,
    )
    parser.add_argument(
        "--dual-screen",
        action="store_true",
        help="Output video to both HDMI ports (raspberry pi 4 only)",
        required=False,
    )
    parser.add_argument(
        "--high-quality",
        action="store_true",
        help="Download higher quality video. Note: requires ffmpeg and may cause CPU, download speed, and other performance issues",
        required=False,
    )
    parser.add_argument(
        "--use-omxplayer",
        action="store_true",
        help="Use OMX Player to play video instead of the default VLC Player. This may be better-performing on older raspberry pi devices. Certain features like key change and cdg support wont be available. Note: if you want to play audio to the headphone jack on a rpi, you'll need to configure this in raspi-config: 'Advanced Options > Audio > Force 3.5mm (headphone)'",
        required=False,
    ),
    parser.add_argument(
        "--use-vlc",
        action="store_true",
        help="Use VLC Player to play video. Enabled by default. Note: if you want to play audio to the headphone jack on a rpi, see troubleshooting steps in README.md",
        required=False,
    ),
    parser.add_argument(
        "--vlc-path",
        help="Full path to VLC (Default: %s)" % default_vlc_path,
        default=default_vlc_path,
        required=False,
    ),
    parser.add_argument(
        "--vlc-port",
        help="HTTP port for VLC remote control api (Default: %s)" % default_vlc_port,
        default=default_vlc_port,
        required=False,
    ),
    parser.add_argument(
        "--logo-path",
        help="Path to a custom logo image file for the splash screen. Recommended dimensions ~ 500x500px",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "--show-overlay",
        action="store_true",
        help="Show overlay on top of video with pikaraoke QR code and IP",
        required=False,
    ),
    parser.add_argument(
        "--admin-password",
        help="Administrator password, for locking down certain features of the web UI such as queue editing, player controls, song editing, and system shutdown. If unspecified, everyone is an admin.",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "--developer-mode",
        help="Run in flask developer mode. Only useful for tweaking the web UI in real time. Will disable the splash screen due to pygame main thread conflicts and may require FLASK_ENV=development env variable for full dev mode features.",
        action="store_true",
        required=False,
    ),
    args = parser.parse_args()

    if (args.admin_password):
        admin_password = args.admin_password

    app.jinja_env.globals.update(filename_from_path=filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

    # Handle OMX player if specified
    if platform == "raspberry_pi" and args.use_omxplayer:
        args.use_vlc = False
    else:
        args.use_vlc = True

    # check if required binaries exist
    if not os.path.isfile(args.youtubedl_path):
        print("Youtube-dl path not found! " + args.youtubedl_path)
        sys.exit(1)
    if args.use_vlc and not os.path.isfile(args.vlc_path):
        print("VLC path not found! " + args.vlc_path)
        sys.exit(1)
    if (
        platform == "raspberry_pi"
        and not args.use_vlc
        and not os.path.isfile(args.omxplayer_path)
    ):
        print("omxplayer path not found! " + args.omxplayer_path)
        sys.exit(1)

    # setup/create download directory if necessary
    dl_path = os.path.expanduser(args.download_path)
    if not dl_path.endswith("/"):
        dl_path += "/"
    if not os.path.exists(dl_path):
        print("Creating download path: " + dl_path)
        os.makedirs(dl_path)

    if (args.developer_mode):
        logging.warning("Splash screen is disabled in developer mode due to main thread conflicts")
        args.hide_splash_screen = True

    # Configure karaoke process
    global k
    k = karaoke.Karaoke(
        port=args.port,
        download_path=dl_path,
        omxplayer_path=args.omxplayer_path,
        youtubedl_path=args.youtubedl_path,
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=args.volume,
        hide_ip=args.hide_ip,
        hide_raspiwifi_instructions=args.hide_raspiwifi_instructions,
        hide_splash_screen=args.hide_splash_screen,
        omxplayer_adev=args.adev,
        dual_screen=args.dual_screen,
        high_quality=args.high_quality,
        use_omxplayer=args.use_omxplayer,
        use_vlc=args.use_vlc,
        vlc_path=args.vlc_path,
        vlc_port=args.vlc_port,
        logo_path=args.logo_path,
        show_overlay=args.show_overlay
    )

    if (args.developer_mode):
        th = threading.Thread(target=k.run)
        th.start()
        app.run(debug=True, port=args.port)
    else:
        # Start the CherryPy WSGI web server
        cherrypy.tree.graft(app, "/")
        # Set the configuration of the web server
        cherrypy.config.update(
            {
                "engine.autoreload.on": False,
                "log.screen": True,
                "server.socket_port": int(args.port),
                "server.socket_host": "0.0.0.0",
            }
        )
        cherrypy.engine.start()
        k.run()
        cherrypy.engine.exit()

    sys.exit()

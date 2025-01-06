import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading

import cherrypy
import flask_babel
import psutil
from flask import (
    Flask,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_babel import Babel
from flask_paginate import Pagination, get_page_parameter

from pikaraoke import VERSION, karaoke
from pikaraoke.constants import LANGUAGES
from pikaraoke.lib.args import parse_pikaraoke_args
from pikaraoke.lib.current_app import get_admin_password, get_karaoke_instance, is_admin
from pikaraoke.lib.ffmpeg import is_ffmpeg_installed
from pikaraoke.lib.file_resolver import delete_tmp_dir
from pikaraoke.lib.get_platform import get_platform, is_raspberry_pi
from pikaraoke.lib.raspi_wifi_config import get_raspi_wifi_text
from pikaraoke.lib.selenium import launch_splash_screen
from pikaraoke.routes.admin import get_admin_bp
from pikaraoke.routes.background_music import get_background_music_bp
from pikaraoke.routes.preferences import get_preferences_bp
from pikaraoke.routes.stream import get_stream_bp

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
raspberry_pi = is_raspberry_pi()
linux = get_platform() == "linux"


# Register blueprints additional routes
app.register_blueprint(get_stream_bp())
app.register_blueprint(get_preferences_bp())
app.register_blueprint(get_admin_bp())
app.register_blueprint(get_background_music_bp())


@babel.localeselector
def get_locale():
    """Select the language to display the webpage in based on the Accept-Language header"""
    if request.args.get("lang"):
        session["lang"] = request.args.get("lang")
        locale = session.get("lang", "en")
    else:
        locale = request.accept_languages.best_match(LANGUAGES.keys())
    return locale


@app.route("/")
def home():
    k = get_karaoke_instance()
    return render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        transpose_value=k.now_playing_transpose,
        admin=is_admin(),
        is_transpose_enabled=k.is_transpose_enabled,
    )


@app.route("/nowplaying")
def nowplaying():
    k = get_karaoke_instance()
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
            "now_playing_duration": k.now_playing_duration,
            "now_playing_transpose": k.now_playing_transpose,
            "now_playing_url": k.now_playing_url,
            "up_next": next_song,
            "next_user": next_user,
            "is_paused": k.is_paused,
            "volume": k.volume,
            # "is_transpose_enabled": k.is_transpose_enabled,
        }
        hash = hashlib.md5(
            json.dumps(rc, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
        ).hexdigest()
        rc["hash"] = hash  # used to detect changes in the now playing data
        return json.dumps(rc)
    except Exception as e:
        logging.error("Problem loading /nowplaying, pikaraoke may still be starting up: " + str(e))
        return ""


# Call this after receiving a command in the front end
@app.route("/clear_command")
def clear_command():
    k = get_karaoke_instance()
    k.now_playing_command = None
    return ""


@app.route("/queue")
def queue():
    k = get_karaoke_instance()
    return render_template(
        "queue.html", queue=k.queue, site_title=site_name, title="Queue", admin=is_admin()
    )


@app.route("/get_queue")
def get_queue():
    k = get_karaoke_instance()
    if len(k.queue) >= 1:
        return json.dumps(k.queue)
    else:
        return json.dumps([])


@app.route("/queue/addrandom", methods=["GET"])
def add_random():
    k = get_karaoke_instance()
    amount = int(request.args["amount"])
    rc = k.queue_add_random(amount)
    if rc:
        # MSG: Message shown after adding random tracks
        flash(_("Added %s random tracks") % amount, "is-success")
    else:
        # MSG: Message shown after running out songs to add during random track addition
        flash(_("Ran out of songs!"), "is-warning")
    return redirect(url_for("queue"))


@app.route("/queue/edit", methods=["GET"])
def queue_edit():
    k = get_karaoke_instance()
    action = request.args["action"]
    if action == "clear":
        k.queue_clear()
        # MSG: Message shown after clearing the queue
        flash(_("Cleared the queue!"), "is-warning")
        return redirect(url_for("queue"))
    else:
        song = request.args["song"]
        song = unquote(song)
        if action == "down":
            result = k.queue_edit(song, "down")
            if result:
                # MSG: Message shown after moving a song down in the queue
                flash(_("Moved down in queue") + ": " + song, "is-success")
            else:
                # MSG: Message shown after failing to move a song down in the queue
                flash(_("Error moving down in queue") + ": " + song, "is-danger")
        elif action == "up":
            result = k.queue_edit(song, "up")
            if result:
                # MSG: Message shown after moving a song up in the queue
                flash(_("Moved up in queue") + ": " + song, "is-success")
            else:
                # MSG: Message shown after failing to move a song up in the queue
                flash(_("Error moving up in queue") + ": " + song, "is-danger")
        elif action == "delete":
            result = k.queue_edit(song, "delete")
            if result:
                # MSG: Message shown after deleting a song from the queue
                flash(_("Deleted from queue") + ": " + song, "is-success")
            else:
                # MSG: Message shown after failing to delete a song from the queue
                flash(_("Error deleting from queue") + ": " + song, "is-danger")
    return redirect(url_for("queue"))


@app.route("/enqueue", methods=["POST", "GET"])
def enqueue():
    k = get_karaoke_instance()
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
    song_title = k.filename_from_path(song)
    return json.dumps({"song": song_title, "success": rc})


@app.route("/skip")
def skip():
    k = get_karaoke_instance()
    k.skip()
    return redirect(url_for("home"))


@app.route("/pause")
def pause():
    k = get_karaoke_instance()
    k.pause()
    return redirect(url_for("home"))


@app.route("/transpose/<semitones>", methods=["GET"])
def transpose(semitones):
    k = get_karaoke_instance()
    k.transpose_current(int(semitones))
    return redirect(url_for("home"))


@app.route("/restart")
def restart():
    k = get_karaoke_instance()
    k.restart()
    return redirect(url_for("home"))


@app.route("/volume/<volume>")
def volume(volume):
    k = get_karaoke_instance()
    k.volume_change(float(volume))
    return redirect(url_for("home"))


@app.route("/vol_up")
def vol_up():
    k = get_karaoke_instance()
    k.vol_up()
    return redirect(url_for("home"))


@app.route("/vol_down")
def vol_down():
    k = get_karaoke_instance()
    k.vol_down()
    return redirect(url_for("home"))


@app.route("/search", methods=["GET"])
def search():
    k = get_karaoke_instance()
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
    k = get_karaoke_instance()
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
    k = get_karaoke_instance()
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
    k = get_karaoke_instance()
    d = request.form.to_dict()
    song = d["song-url"]
    user = d["song-added-by"]
    title = d["song-title"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=k.download_video, args=[song, queue, user, title])
    t.daemon = True
    t.start()

    displayed_title = title if title else song
    flash_message = (
        # MSG: Message shown after starting a download. Song title is displayed in the message.
        _("Download started: %s. This may take a couple of minutes to complete.")
        % displayed_title
    )

    if queue:
        # MSG: Message shown after starting a download that will be adding a song to the queue.
        flash_message += _("Song will be added to queue.")
    else:
        # MSG: Message shown after after starting a download.
        flash_message += _('Song will appear in the "available songs" list.')
    flash(flash_message, "is-info")
    return redirect(url_for("search"))


@app.route("/qrcode")
def qrcode():
    k = get_karaoke_instance()
    return send_file(k.qr_codei9ew38905_path, mimetype="image/png")


@app.route("/logo")
def logo():
    k = get_karaoke_instance()
    return send_file(k.logo_path, mimetype="image/png")


@app.route("/end_song", methods=["GET", "POST"])
def end_song():
    k = get_karaoke_instance()
    d = request.form.to_dict()
    reason = d["reason"] if "reason" in d else None
    k.end_song(reason)
    return "ok"


@app.route("/start_song", methods=["GET"])
def start_song():
    k = get_karaoke_instance()
    k.start_song()
    return "ok"


@app.route("/files/delete", methods=["GET"])
def delete_file():
    k = get_karaoke_instance()
    if "song" in request.args:
        song_path = request.args["song"]
        exists = any(item.get("file") == song_path for item in k.queue)
        if exists:
            flash(
                # MSG: Message shown after trying to delete a song that is in the queue.
                _("Error: Can't delete this song because it is in the current queue")
                + ": "
                + song_path,
                "is-danger",
            )
        else:
            k.delete(song_path)
            # MSG: Message shown after deleting a song. Followed by the song path
            flash(_("Song deleted: %s") % k.filename_from_path(song_path), "is-warning")
    else:
        # MSG: Message shown after trying to delete a song without specifying the song.
        flash(_("Error: No song specified!"), "is-danger")
    return redirect(url_for("browse"))


@app.route("/files/edit", methods=["GET", "POST"])
def edit_file():
    k = get_karaoke_instance()
    # MSG: Message shown after trying to edit a song that is in the queue.
    queue_error_msg = _("Error: Can't edit this song because it is in the current queue: ")
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
                        # MSG: Message shown after trying to rename a file to a name that already exists.
                        _("Error renaming file: '%s' to '%s', Filename already exists")
                        % (old_name, new_name + file_extension),
                        "is-danger",
                    )
                else:
                    k.rename(old_name, new_name)
                    flash(
                        # MSG: Message shown after renaming a file.
                        _("Renamed file: %s to %s") % (old_name, new_name),
                        "is-warning",
                    )
        else:
            # MSG: Message shown after trying to edit a song without specifying the filename.
            flash(_("Error: No filename parameters were specified!"), "is-danger")
        return redirect(url_for("browse"))


@app.route("/splash")
def splash():
    k = get_karaoke_instance()
    # Only do this on Raspberry Pis
    if raspberry_pi:
        status = subprocess.run(["iwconfig", "wlan0"], stdout=subprocess.PIPE).stdout.decode(
            "utf-8"
        )
        text = ""
        if "Mode:Master" in status:
            # handle raspiwifi connection mode
            text = get_raspi_wifi_text()
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
        disable_bg_music=k.disable_bg_music,
        disable_score=k.disable_score,
        bg_music_volume=k.bg_music_volume,
    )


@app.route("/info")
def info():
    k = get_karaoke_instance()
    url = k.url
    admin_password = get_admin_password()

    # cpu
    try:
        cpu = str(psutil.cpu_percent()) + "%"
    except:
        cpu = _("CPU usage query unsupported")

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
        is_transpose_enabled=k.is_transpose_enabled,
        youtubedl_version=youtubedl_version,
        platform=k.platform,
        os_version=k.os_version,
        is_pi=raspberry_pi,
        is_linux=linux,
        pikaraoke_version=VERSION,
        admin=is_admin(),
        admin_enabled=admin_password != None,
        disable_bg_music=k.disable_bg_music,
        bg_music_volume=int(100 * k.bg_music_volume),
        disable_score=k.disable_score,
        hide_url=k.hide_url,
        limit_user_songs_by=k.limit_user_songs_by,
        hide_notifications=k.hide_notifications,
        hide_overlay=k.hide_overlay,
        normalize_audio=k.normalize_audio,
        complete_transcode_before_play=k.complete_transcode_before_play,
        high_quality_audio=k.high_quality,
        splash_delay=k.splash_delay,
        screensaver_timeout=k.screensaver_timeout,
        volume=int(100 * k.volume),
        buffer_size=k.buffer_size,
    )


def main():
    platform = get_platform()

    args = parse_pikaraoke_args()

    if not is_ffmpeg_installed():
        logging.error(
            "ffmpeg is not installed, which is required to run PiKaraoke. See: https://www.ffmpeg.org/"
        )
        sys.exit(1)

    # setup/create download directory if necessary
    if not os.path.exists(args.download_path):
        print("Creating download path: " + args.download_path)
        os.makedirs(args.download_path)

    # Configure karaoke process
    k = karaoke.Karaoke(
        port=args.port,
        download_path=args.download_path,
        youtubedl_path=args.youtubedl_path,
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=args.volume,
        normalize_audio=args.normalize_audio,
        complete_transcode_before_play=args.complete_transcode_before_play,
        buffer_size=args.buffer_size,
        hide_url=args.hide_url,
        hide_notifications=args.hide_notifications,
        hide_splash_screen=args.hide_splash_screen,
        high_quality=args.high_quality,
        logo_path=args.logo_path,
        hide_overlay=args.hide_overlay,
        screensaver_timeout=args.screensaver_timeout,
        url=args.url,
        prefer_hostname=args.prefer_hostname,
        disable_bg_music=args.disable_bg_music,
        bg_music_volume=args.bg_music_volume,
        bg_music_path=args.bg_music_path,
        disable_score=args.disable_score,
        limit_user_songs_by=args.limit_user_songs_by,
        config_file_path=args.config_file_path,
    )

    # expose karaoke object to the flask app
    with app.app_context():
        app.k = k

    # expose shared configuration variables to the flask app
    app.config["ADMIN_PASSWORD"] = args.admin_password

    # Expose some functions to jinja templates
    app.jinja_env.globals.update(filename_from_path=k.filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

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

    # Handle sigterm, apparently cherrypy won't shut down without explicit handling
    signal.signal(signal.SIGTERM, lambda signum, stack_frame: k.stop())

    # force headless mode when on Android
    if (platform == "android") and not args.hide_splash_screen:
        args.hide_splash_screen = True
        logging.info("Forced to run headless mode in Android")

    # Start the splash screen using selenium
    if not args.hide_splash_screen:
        driver = launch_splash_screen(k, args.window_size)
        if not driver:
            cherrypy.engine.exit()
            sys.exit()
    else:
        driver = None

    # Start the karaoke process
    k.run()

    # Close running processes when done
    if driver is not None:
        driver.close()
    cherrypy.engine.exit()
    delete_tmp_dir()
    sys.exit()


if __name__ == "__main__":
    main()

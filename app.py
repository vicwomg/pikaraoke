import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

import cherrypy
import karaoke
import psutil
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

try:
    from urllib.parse import quote, unquote
except ImportError:
    from urllib import quote, unquote


app = Flask(__name__)
app.secret_key = "HjI981293u99as811lll"
site_name = "PiKaraoke"


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


def is_raspberry_pi():
    return os.uname()[4][:3] == "arm"


@app.route("/")
def home():
    return render_template("home.html", site_title=site_name, title="Home")


@app.route("/nowplaying")
def nowplaying():
    if len(k.queue) >= 2:
        next_song = filename_from_path(k.queue[1])
    else:
        next_song = None
    rc = {"now_playing": k.now_playing, "up_next": next_song, "is_pause": k.is_pause}
    return json.dumps(rc)


@app.route("/queue")
def queue():
    return render_template(
        "queue.html", queue=k.queue, site_title=site_name, title="Queue"
    )


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
        song = d["song_to_add"]
    rc = k.enqueue(song)
    if rc:
        flash("Song added to queue: " + filename_from_path(song), "is-success")
    else:
        flash("Song is already in queue: " + filename_from_path(song), "is-danger")
    return redirect(url_for("home"))


@app.route("/skip")
def skip():
    k.skip()
    return redirect(url_for("home"))


@app.route("/pause")
def pause():
    k.pause()
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


@app.route("/browse", methods=["GET"])
def browse():
    if "sort" in request.args and request.args["sort"] == "date":
        songs = sorted(k.available_songs, key=lambda x: os.path.getctime(x))
        songs.reverse()
        sort_order = "Date"
    else:
        songs = k.available_songs
        sort_order = "Alphabetical"
    return render_template(
        "files.html",
        sort_order=sort_order,
        site_title=site_name,
        title="Browse",
        songs=songs,
    )


@app.route("/download", methods=["POST"])
def download():
    d = request.form.to_dict()
    song = d["song-url"]
    if "queue" in d and d["queue"] == "on":
        queue = True
    else:
        queue = False

    # download in the background since this can take a few minutes
    t = threading.Thread(target=k.download_video, args=[song, queue])
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


@app.route("/qrcode")
def qrcode():
    return send_file(k.generate_qr_code(), mimetype="image/png")


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
            if old_name in k.queue:
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

    return render_template(
        "info.html",
        site_title=site_name,
        title="Info",
        url=url,
        memory=memory,
        cpu=cpu,
        disk=disk,
        youtubedl_version=youtubedl_version,
        is_raspberry_pi=is_raspberry_pi(),
    )


# Delay system commands to allow redirect to render first
def delayed_halt(cmd):
    time.sleep(3)
    k.queue_clear()  # stop all pending omxplayer processes
    if cmd == 0:
        cherrypy.engine.stop()
        cherrypy.engine.exit()
        k.stop()
        sys.exit()
    if cmd == 1:
        os.system("shutdown now")
    if cmd == 2:
        os.system("reboot")


update_log_path = "/tmp/youtube-dl-update.log"


def update_youtube_dl():
    time.sleep(3)
    os.system('echo "Current youtube-dl version: " > %s' % update_log_path)
    os.system("youtube-dl --version >> %s" % update_log_path)
    os.system("pip install --upgrade youtube_dl >> %s" % update_log_path)
    os.system('echo "New youtube-dl version: " >> %s' % update_log_path)
    os.system("youtube-dl --version >> %s" % update_log_path)
    k.get_youtubedl_version()


@app.route("/update_ytdl")
def update_ytdl():
    flash(
        "Updating youtube-dl! Should take a minute or two... (log output at: %s)"
        % update_log_path,
        "is-warning",
    )
    th = threading.Thread(target=update_youtube_dl)
    th.start()
    return redirect(url_for("home"))


@app.route("/quit")
def quit():
    flash("Quitting to console now!", "is-warning")
    th = threading.Thread(target=delayed_halt, args=[0])
    th.start()
    return redirect(url_for("home"))


@app.route("/shutdown")
def shutdown():
    flash("Shutting down system now!", "is-danger")
    th = threading.Thread(target=delayed_halt, args=[1])
    th.start()
    return redirect(url_for("home"))


@app.route("/reboot")
def reboot():
    flash("Rebooting system now!", "is-danger")
    th = threading.Thread(target=delayed_halt, args=[2])
    th.start()
    return redirect(url_for("home"))


# Handle sigterm, apparently cherrypy won't shut down without explicit handling
signal.signal(signal.SIGTERM, lambda signum, stack_frame: sys.exit(1))

if __name__ == "__main__":

    default_port = 5000
    default_volume = 0
    default_splash_delay = 5
    default_log_level = logging.INFO
    default_dl_dir = (
        "/usr/lib/pikaraoke/songs" if is_raspberry_pi() else "~/pikaraoke/songs"
    )
    default_omxplayer_path = "/usr/bin/omxplayer"
    default_youtubedl_path = "/usr/local/bin/youtube-dl"

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
        "--show-overlay",
        action="store_true",
        help="Show text overlay in omxplayer with song title and IP. (feature is broken on Pi 4 omxplayer 12/24/2019)",
        required=False,
    )
    parser.add_argument(
        "--hide-ip",
        action="store_true",
        help="Hide IP address from the screen.",
        required=False,
    )
    parser.add_argument(
        "--hide-splash-screen",
        action="store_true",
        help="Hide splash screen before/between songs.",
        required=False,
    )
    parser.add_argument(
        "--alsa-fix",
        action="store_true",
        help="Add this if you are using a rpi USB soundcard or Hifi audio hat and cannot hear audio.",
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
        "--use-vlc",
        action="store_true",
        help="Use VLC Player instead of the default OMX Player. Enabled by default on non-pi hardware. Note: if you want to play audio to the headphone jack on a rpi, you'll need to configure this in raspi-config: 'Advanced Options > Audio > Force 3.5mm (headphone)'",
        required=False,
    ),
    args = parser.parse_args()

    app.jinja_env.globals.update(filename_from_path=filename_from_path)
    app.jinja_env.globals.update(url_escape=quote)

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

    # Start the CherryPy WSGI web server
    cherrypy.engine.start()

    # Start karaoke process
    global k
    k = karaoke.Karaoke(
        port=args.port,
        download_path=args.download_path,
        omxplayer_path=args.omxplayer_path,
        youtubedl_path=args.youtubedl_path,
        splash_delay=args.splash_delay,
        log_level=args.log_level,
        volume=args.volume,
        hide_overlay=not args.show_overlay,
        hide_ip=args.hide_ip,
        hide_splash_screen=args.hide_splash_screen,
        alsa_fix=args.alsa_fix,
        dual_screen=args.dual_screen,
        high_quality=args.high_quality,
        use_vlc=args.use_vlc,
    )
    k.run()

    cherrypy.engine.exit()
    sys.exit()

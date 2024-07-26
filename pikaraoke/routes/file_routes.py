from pathlib import Path

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from pikaraoke import get_current_app

file_bp = Blueprint("file", __name__)


@file_bp.route("/files/delete", methods=["GET"])
def delete_file():
    current_app = get_current_app()
    if "song" in request.args:
        song_path = request.args["song"]
        if song_path in current_app.karaoke.queue:
            flash(
                "Error: Can't delete this song because it is in the current queue: " + song_path,
                "is-danger",
            )
        else:
            current_app.karaoke.delete(Path(song_path))
            flash("Song deleted: " + song_path, "is-warning")
    else:
        flash("Error: No song parameter specified!", "is-danger")

    return redirect(url_for("home.browse"))


@file_bp.route("/files/edit", methods=["GET", "POST"])
def edit_file():
    current_app = get_current_app()
    queue_error_msg = "Error: Can't edit this song because it is in the current queue: "
    if "song" in request.args:
        song_path = request.args["song"]
        # print "SONG_PATH" + song_path
        if song_path in current_app.karaoke.queue:
            flash(queue_error_msg + song_path, "is-danger")
            return redirect(url_for("home.browse"))

        return render_template(
            "edit.html",
            site_title=current_app.config["SITE_NAME"],
            title="Song File Edit",
            song=song_path.encode("utf-8", "ignore"),
        )

    d = request.form.to_dict()
    if "new_file_name" in d and "old_file_name" in d:
        new_name = d["new_file_name"]
        old_name = d["old_file_name"]
        if current_app.karaoke.is_song_in_queue(old_name):
            # check one more time just in case someone added it during editing
            flash(queue_error_msg + song_path, "is-danger")
        else:
            # check if new_name already exist
            file_extension = Path(old_name).suffix
            new_file_path = (
                Path(current_app.karaoke.download_path)
                .joinpath(new_name)
                .with_suffix(file_extension)
            )
            if new_file_path.is_file():
                flash(
                    "Error Renaming file: '%s' to '%s'. Filename already exists."
                    % (old_name, new_name + file_extension),
                    "is-danger",
                )
            else:
                current_app.karaoke.rename(old_name, new_name)
                flash(
                    "Renamed file: '%s' to '%s'." % (old_name, new_name),
                    "is-warning",
                )
    else:
        flash("Error: No filename parameters were specified!", "is-danger")
    return redirect(url_for("home.browse"))


@file_bp.route("/logo")
def logo():
    current_app = get_current_app()
    return send_file(current_app.karaoke.logo_path, mimetype="image/png")


@file_bp.route("/qrcode")
def qrcode():
    current_app = get_current_app()
    return send_file(current_app.karaoke.qr_code_path, mimetype="image/png")

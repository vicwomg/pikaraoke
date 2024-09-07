import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from pikaraoke import translate
from pikaraoke.lib.logger import log_endpoint_access

auth_bp = Blueprint(
    "auth",
    __name__,
)


@auth_bp.route("/auth", methods=["POST"])
@log_endpoint_access
def auth():
    d = request.form.to_dict()
    pw = d.get("admin-password")
    if pw == current_app.config["ADMIN_PASSWORD"]:
        resp = make_response(redirect("/"))
        expire_date = datetime.datetime.now() + datetime.timedelta(days=90)
        resp.set_cookie("admin", current_app.config["ADMIN_PASSWORD"], expires=expire_date)
        flash(translate("Admin mode granted!"), "is-success")
    else:
        resp = make_response(redirect(url_for("auth.login")))
        flash(translate("Incorrect admin password!"), "is-danger")
    return resp


@auth_bp.route("/login")
@log_endpoint_access
def login():
    return render_template("login.html")


@auth_bp.route("/logout")
@log_endpoint_access
def logout():
    resp = make_response(redirect("/"))
    resp.set_cookie("admin", "")
    flash("Logged out of admin mode!", "is-success")
    return resp

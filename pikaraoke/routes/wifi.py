import flask_babel
from flask import Blueprint, render_template, request, flash, redirect, url_for
from pikaraoke.lib.current_app import is_admin
from pikaraoke.lib.setup_wifi import get_all_wifi, connect_to_wifi, get_current_wifi
from pywifi.iface import Interface
import pywifi
_ = flask_babel.gettext

wifi_bp = Blueprint("wifi", __name__)
wifi = pywifi.PyWiFi()
iface: Interface = wifi.interfaces()[0]

@wifi_bp.route("/wifi")
def wifi_settings():
    if not is_admin():
        flash(_("You don't have permission to change WiFi settings"), "is-danger")
        return redirect(url_for("info.info"))
    
    networks = []
    return render_template("wifi.html", networks=networks)

@wifi_bp.route("/connect_wifi", methods=["POST"])
def connect_wifi():
    if not is_admin():
        return {"success": False, "message": _("Permission denied")}
    
    ssid = request.form.get("ssid")
    password = request.form.get("password")
    
    success = connect_to_wifi(iface, ssid, password)
    if success:
        return {"success": success, "message": _("Connected successfully")}
    else:
        return {"success": success, "message": _("Connection failed")}

@wifi_bp.route("/current_wifi",methods=["GET"])
def get_current_wifi_info():
    current_wifi = get_current_wifi(iface)
    if current_wifi is not None:
        return {"success": True, "status":"connected", "ssid":current_wifi}
    else:
        return {"success": False, "status":"disconnected","ssid":None}

@wifi_bp.route("/available_wifi",methods=["GET"])
def get_available_wifi_info():
    networks = get_all_wifi(iface)
    return {"success": True, "networks":networks}
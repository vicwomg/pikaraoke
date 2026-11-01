"""Wi-Fi setup endpoints, admin-only and present only on a supported Pi.

JSON the Settings page polls: the current network, a live scan, and a connect
that answers before it switches, since joining tears down the hotspot the
phone is on, which would otherwise lose the server before the reply arrives.
"""

from flask import abort, jsonify
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance

wifi_api_bp = Blueprint("wifi_api", __name__)


class ConnectForm(Schema):
    name = fields.String(required=True, metadata={"description": "Network name to join"})
    password = fields.String(load_default="", metadata={"description": "Network password"})


def _require_supported():
    """Return the manager, or 404 where Wi-Fi setup does not apply (not a Pi)."""
    wifi = get_karaoke_instance().wifi_manager
    if not wifi.supported:
        abort(404)
    return wifi


def _state_payload(wifi) -> dict:
    state = wifi.state()
    on_hotspot = state.kind == "hotspot"
    return {
        "kind": state.kind,
        "network": state.network,
        "ethernet_count": state.ethernet_count,
        "hotspot_name": wifi.hotspot_name if on_hotspot else None,
        "hotspot_password": wifi.hotspot_password if on_hotspot else None,
    }


@wifi_api_bp.route("/api/wifi/state")
def wifi_state():
    """The current network, and the hotspot's details while it is up."""
    return jsonify(_state_payload(_require_supported()))


@wifi_api_bp.route("/api/wifi/networks")
def wifi_networks():
    """A live scan of nearby networks, strongest first."""
    return jsonify(_require_supported().networks())


@wifi_api_bp.route("/api/wifi/connect", methods=["POST"])
@wifi_api_bp.arguments(ConnectForm, location="form")
def wifi_connect(form):
    """Start joining a network, answering before the attempt drops the hotspot."""
    k = get_karaoke_instance()
    _require_supported()
    k.start_wifi_connect(form["name"], form["password"])
    return jsonify({"status": "switching", "network": form["name"]})

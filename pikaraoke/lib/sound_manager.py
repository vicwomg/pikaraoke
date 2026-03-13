"""Server-side microphone passthrough manager.

Enumerates system audio input devices and manages passthrough from
microphone inputs to speakers. Uses PulseAudio/PipeWire on Linux
(via pactl) and sounddevice (PortAudio) on Windows/macOS.
"""

import json
import logging
import re
import shutil
import struct
import subprocess

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.get_platform import is_linux
from pikaraoke.lib.preference_manager import PreferenceManager

_HAS_PACTL = is_linux() and shutil.which("pactl") is not None

# sounddevice is only needed on non-Linux platforms
_SOUNDDEVICE_AVAILABLE = False
if not _HAS_PACTL:
    try:
        import sounddevice as sd

        _SOUNDDEVICE_AVAILABLE = True
    except (ImportError, OSError):
        logging.warning(
            "sounddevice not available (missing PortAudio?). Microphone support disabled."
        )

_MAX_GAIN = 2.0
_DEFAULT_LATENCY_MS = 50
_MIN_LATENCY_MS = 10
_MAX_LATENCY_MS = 200

# Windows virtual/alias device names that duplicate real devices
_VIRTUAL_INPUT_DEVICE_NAMES = {
    "Microsoft Sound Mapper - Input",
    "Primary Sound Capture Driver",
}


def _pactl_list_sources() -> list[dict]:
    """List PulseAudio/PipeWire input sources with descriptions via a single pactl call."""
    try:
        result = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    sources = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Source #"):
            if current.get("name") and ".monitor" not in current["name"]:
                sources.append(current)
            current = {"deviceId": stripped.split("#", 1)[1]}
        name_match = re.match(r"Name:\s+(.+)", stripped)
        if name_match:
            current["name"] = name_match.group(1)
        desc_match = re.match(r"Description:\s+(.+)", stripped)
        if desc_match:
            current["description"] = desc_match.group(1)

    # Don't forget the last source
    if current.get("name") and ".monitor" not in current["name"]:
        sources.append(current)

    return [
        {
            "deviceId": s["deviceId"],
            "label": s.get("description", s["name"]),
            "paSource": s["name"],
        }
        for s in sources
    ]


class _ActiveMic:
    """State for a single active microphone."""

    def __init__(
        self,
        device_id: str,
        loopback_module_id: str | None,
        stream=None,
        mic_state: list[float] | None = None,
        echo_module_id: str | None = None,
    ) -> None:
        self.device_id = device_id
        self.loopback_module_id = loopback_module_id
        self.stream = stream
        self.mic_state = mic_state
        self.echo_module_id = echo_module_id


class SoundManager:
    """Manages microphone enumeration and audio passthrough.

    On Linux (with PulseAudio or PipeWire), uses pactl module-loopback for
    zero-code audio routing. On Windows/macOS, uses sounddevice (PortAudio).
    """

    def __init__(self, preferences: PreferenceManager, events: EventSystem) -> None:
        self._preferences = preferences
        self._events = events
        self._active_mics: dict[str, _ActiveMic] = {}
        self._device_list: list[dict] = []

    @property
    def available(self) -> bool:
        return _HAS_PACTL or _SOUNDDEVICE_AVAILABLE

    def enumerate_devices(self) -> list[dict]:
        """Query system audio input devices."""
        if _HAS_PACTL:
            return self._enumerate_pactl()
        if _SOUNDDEVICE_AVAILABLE:
            return self._enumerate_sounddevice()
        return []

    def _enumerate_pactl(self) -> list[dict]:
        """Enumerate mic devices via pactl."""
        self._device_list = _pactl_list_sources()
        logging.info(f"Mic devices enumerated (pactl): {[m['label'] for m in self._device_list]}")
        return self._device_list

    def _enumerate_sounddevice(self) -> list[dict]:
        """Enumerate mic devices via sounddevice (Windows/macOS)."""
        if not self._active_mics:
            _reinit_portaudio()

        try:
            devices = sd.query_devices()
        except sd.PortAudioError as e:
            logging.error(f"Failed to query audio devices: {e}")
            return []

        preferred_hostapi = None
        try:
            default_out = sd.query_devices(kind="output")
            preferred_hostapi = default_out["hostapi"]
        except (sd.PortAudioError, ValueError):
            pass

        has_any_output = any(dev["max_output_channels"] > 0 for dev in devices)
        if not has_any_output:
            logging.warning("No audio output devices found; mic passthrough unavailable")
            return []

        mics = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] <= 0:
                continue
            if dev["name"] in _VIRTUAL_INPUT_DEVICE_NAMES:
                continue
            if preferred_hostapi is not None and dev["hostapi"] != preferred_hostapi:
                continue
            mics.append({"deviceId": str(i), "label": dev["name"]})

        self._device_list = mics
        logging.info(f"Mic devices enumerated (sounddevice): {[m['label'] for m in mics]}")
        return mics

    def get_enriched_devices(self, settings: dict | None = None) -> list[dict]:
        """Return device list merged with saved preference settings."""
        if settings is None:
            settings = self.load_settings()

        enriched = []
        for dev in self._device_list:
            label = dev["label"]
            saved = settings.get(label, {})
            enriched.append(
                {
                    "deviceId": dev["deviceId"],
                    "label": label,
                    "enabled": saved.get("enabled", False),
                    "volume": saved.get("volume", 1.0),
                }
            )
        return enriched

    def get_mic_settings_state(self) -> dict:
        """Return latency and echo-cancel state from a single settings load."""
        settings = self.load_settings()
        return {
            "latency_ms": int(settings.get("_latency_ms", _DEFAULT_LATENCY_MS)),
            "echo_cancel": bool(settings.get("_echo_cancel", False)),
        }

    def get_latency_ms(self) -> int:
        """Get the configured mic loopback latency in milliseconds."""
        settings = self.load_settings()
        return int(settings.get("_latency_ms", _DEFAULT_LATENCY_MS))

    def set_latency_ms(self, latency_ms: int) -> None:
        """Set mic loopback latency and restart active mics to apply it."""
        latency_ms = max(_MIN_LATENCY_MS, min(_MAX_LATENCY_MS, latency_ms))
        settings = self.load_settings()
        settings["_latency_ms"] = latency_ms
        self.save_settings(settings)
        logging.info(f"Mic latency set to {latency_ms}ms")
        self._restart_active_mics()

    def get_echo_cancel(self) -> bool:
        """Whether echo cancellation is enabled for mic loopback (Linux only)."""
        settings = self.load_settings()
        return bool(settings.get("_echo_cancel", False))

    def set_echo_cancel(self, enabled: bool) -> None:
        """Enable or disable echo cancellation and restart active mics."""
        settings = self.load_settings()
        settings["_echo_cancel"] = enabled
        self.save_settings(settings)
        logging.info(f"Echo cancellation {'enabled' if enabled else 'disabled'}")
        self._restart_active_mics()

    def _restart_active_mics(self) -> None:
        """Deactivate and re-activate all active mics to apply changed settings."""
        settings = self.load_settings()
        active_snapshot = [
            (mic.device_id, self._get_active_volume(mic, settings))
            for mic in self._active_mics.values()
        ]
        self.stop()
        for device_id, volume in active_snapshot:
            self.activate(device_id, volume)

    def _get_active_volume(self, mic: _ActiveMic, settings: dict | None = None) -> float:
        """Read the current volume from an active mic."""
        if mic.mic_state is not None:
            return mic.mic_state[0]
        # For pactl mics, look up saved volume from settings
        dev = self._find_device(mic.device_id)
        if dev:
            if settings is None:
                settings = self.load_settings()
            saved = settings.get(dev["label"], {})
            return saved.get("volume", 1.0)
        return 1.0

    def activate(self, device_id: str, volume: float) -> bool:
        """Start audio passthrough for a microphone device."""
        if device_id in self._active_mics:
            self.update_volume(device_id, volume)
            return True

        if _HAS_PACTL:
            return self._activate_pactl(device_id, volume)
        if _SOUNDDEVICE_AVAILABLE:
            return self._activate_sounddevice(device_id, volume)
        return False

    def _activate_pactl(self, device_id: str, volume: float) -> bool:
        """Activate mic passthrough via PulseAudio module-loopback.

        When echo cancellation is enabled, loads module-echo-cancel first
        to create a processed source, then loops that through to speakers.
        """
        dev = self._find_device(device_id)
        if dev is None:
            logging.error(f"Device {device_id} not found in device list")
            return False

        source_name = dev.get("paSource", dev["label"])
        vol_percent = int(min(volume, _MAX_GAIN) * 100)
        echo_module_id = None

        # Optionally load echo cancellation
        if self.get_echo_cancel():
            echo_module_id = self._load_echo_cancel_module(source_name)
            if echo_module_id:
                source_name = f"{source_name}.echo-cancel"

        try:
            result = subprocess.run(
                [
                    "pactl",
                    "load-module",
                    "module-loopback",
                    f"source={source_name}",
                    f"latency_msec={self.get_latency_ms()}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logging.error(f"pactl load-module failed: {result.stderr.strip()}")
                self._unload_module(echo_module_id)
                return False
            module_id = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logging.error(f"Failed to load loopback module: {e}")
            self._unload_module(echo_module_id)
            return False

        # Set source volume
        try:
            subprocess.run(
                ["pactl", "set-source-volume", source_name, f"{vol_percent}%"],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        active = _ActiveMic(device_id=device_id, loopback_module_id=module_id)
        active.echo_module_id = echo_module_id
        self._active_mics[device_id] = active
        logging.info(f"Mic activated (pactl loopback module {module_id}): {dev['label']}")
        return True

    def _activate_sounddevice(self, device_id: str, volume: float) -> bool:
        """Activate mic passthrough via sounddevice (Windows/macOS).

        Uses a duplex RawStream with separate input/output channel counts
        to handle mono mics routed to stereo outputs.
        """
        idx = int(device_id)
        try:
            dev_info = sd.query_devices(idx)
        except sd.PortAudioError as e:
            logging.error(f"Failed to query device {device_id}: {e}")
            return False

        output_idx = _find_output_for_hostapi(dev_info["hostapi"])
        if output_idx is None:
            logging.error(f"No output device found on same host API as {dev_info['name']}")
            return False

        try:
            out_info = sd.query_devices(output_idx)
        except sd.PortAudioError as e:
            logging.error(f"Failed to query output device {output_idx}: {e}")
            return False

        logging.info(
            f"Mic routing: '{dev_info['name']}' (idx={idx}) -> '{out_info['name']}' (idx={output_idx})"
        )

        in_channels = min(dev_info["max_input_channels"], 2)
        out_channels = min(out_info["max_output_channels"], 2)
        samplerate = int(dev_info["default_samplerate"])
        gain = min(volume, _MAX_GAIN)
        mono_to_stereo = in_channels == 1 and out_channels == 2

        # Mutable container so the callback can read updated gain values
        mic_state = [gain]

        def callback(indata, outdata, frames, time_info, status):
            if status:
                logging.debug(f"Mic stream status ({device_id}): {status}")
            current_gain = mic_state[0]

            if current_gain <= 0.0:
                outdata[:] = b"\x00" * len(outdata)
                return

            if current_gain == 1.0:
                source = indata
            else:
                source = _apply_gain(indata, current_gain)

            if mono_to_stereo:
                # Duplicate each mono sample for left and right channels
                n_samples = len(source) // 2
                samples = struct.unpack(f"<{n_samples}h", source)
                outdata[:] = struct.pack(
                    f"<{n_samples * 2}h",
                    *[s for sample in samples for s in (sample, sample)],
                )
            else:
                outdata[:] = source

        try:
            stream = sd.RawStream(
                device=(idx, output_idx),
                samplerate=samplerate,
                channels=(in_channels, out_channels),
                dtype="int16",
                callback=callback,
                latency="low",
            )
            stream.start()
        except sd.PortAudioError as e:
            logging.error(f"Failed to activate mic {device_id}: {e}")
            return False

        active = _ActiveMic(
            device_id=device_id, loopback_module_id=None, stream=stream, mic_state=mic_state
        )
        self._active_mics[device_id] = active
        logging.info(
            f"Mic activated (sounddevice): {dev_info['name']} "
            f"({in_channels}ch -> {out_channels}ch @ {samplerate}Hz)"
        )
        return True

    def _load_echo_cancel_module(self, source_name: str) -> str | None:
        """Load PulseAudio module-echo-cancel for a source. Returns module ID or None."""
        try:
            result = subprocess.run(
                [
                    "pactl",
                    "load-module",
                    "module-echo-cancel",
                    f"source_name={source_name}.echo-cancel",
                    f"source_master={source_name}",
                    "aec_method=webrtc",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logging.warning(
                    f"Echo cancel module failed (continuing without): {result.stderr.strip()}"
                )
                return None
            logging.info(f"Echo cancellation loaded for source: {source_name}")
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logging.warning(f"Echo cancel module unavailable: {e}")
            return None

    def _unload_module(self, module_id: str | None) -> None:
        """Unload a PulseAudio module by ID, if set."""
        if not module_id:
            return
        try:
            subprocess.run(
                ["pactl", "unload-module", module_id],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logging.warning(f"Failed to unload module {module_id}: {e}")

    def deactivate(self, device_id: str) -> None:
        """Stop audio passthrough for a microphone device."""
        active = self._active_mics.pop(device_id, None)
        if not active:
            return

        if active.loopback_module_id is not None:
            self._unload_module(active.loopback_module_id)
            self._unload_module(active.echo_module_id)
        elif active.stream is not None:
            try:
                active.stream.stop()
                active.stream.close()
            except (sd.PortAudioError, OSError) as e:
                logging.warning(f"Error closing mic stream {device_id}: {e}")

        logging.info(f"Mic deactivated: device={device_id}")

    def update_volume(self, device_id: str, volume: float) -> None:
        """Update gain for an active microphone."""
        active = self._active_mics.get(device_id)
        if not active:
            return

        gain = min(volume, _MAX_GAIN)

        if active.loopback_module_id is not None:
            # Update volume via pactl
            dev = self._find_device(device_id)
            if dev:
                source_name = dev.get("paSource", dev["label"])
                vol_percent = int(gain * 100)
                try:
                    subprocess.run(
                        ["pactl", "set-source-volume", source_name, f"{vol_percent}%"],
                        capture_output=True,
                        timeout=5,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
        elif active.mic_state is not None:
            active.mic_state[0] = gain

        logging.debug(f"Mic volume updated: device={device_id} vol={gain}")

    def refresh(self) -> list[dict]:
        """Re-enumerate devices and return enriched list."""
        self.enumerate_devices()
        return self.get_enriched_devices()

    def start(self) -> None:
        """Enumerate devices and re-enable saved mics."""
        self.enumerate_devices()

        settings = self.load_settings()

        for dev in self._device_list:
            label = dev["label"]
            saved = settings.get(label, {})
            if saved.get("enabled", False):
                volume = saved.get("volume", 1.0)
                if self.activate(dev["deviceId"], volume):
                    logging.info(f"Auto-activated saved mic: {label}")

    def stop(self) -> None:
        """Close all active mic streams."""
        for device_id in list(self._active_mics.keys()):
            self.deactivate(device_id)

    def _find_device(self, device_id: str) -> dict | None:
        """Find a device dict by its ID."""
        for dev in self._device_list:
            if dev["deviceId"] == device_id:
                return dev
        return None

    def load_settings(self) -> dict:
        """Load and parse mic settings from preferences."""
        raw = self._preferences.get_or_default("mic_settings") or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logging.warning("mic_settings preference is malformed JSON; resetting")
            return {}

    def save_settings(self, settings: dict) -> None:
        """Persist mic settings to preferences."""
        self._preferences.set("mic_settings", json.dumps(settings))


def _apply_gain(data: bytes, gain: float) -> bytes:
    """Apply gain to int16 PCM audio data with clipping protection."""

    n_samples = len(data) // 2
    samples = struct.unpack(f"<{n_samples}h", data)
    return struct.pack(
        f"<{n_samples}h",
        *(max(-32768, min(32767, int(s * gain))) for s in samples),
    )


def _reinit_portaudio() -> None:
    """Force PortAudio to re-scan the system device list."""
    if not _SOUNDDEVICE_AVAILABLE:
        return
    try:
        sd._terminate()
        sd._initialize()
    except (sd.PortAudioError, OSError) as e:
        logging.warning(f"PortAudio re-init failed: {e}")


def _find_output_for_hostapi(hostapi_index: int) -> int | None:
    """Find the default output device on the given host API."""
    if not _SOUNDDEVICE_AVAILABLE:
        return None
    try:
        hostapi_info = sd.query_hostapis(hostapi_index)
        default_output = hostapi_info.get("default_output_device", -1)
        if default_output >= 0:
            return default_output
    except sd.PortAudioError:
        pass
    for i, dev in enumerate(sd.query_devices()):
        if dev["hostapi"] == hostapi_index and dev["max_output_channels"] > 0:
            return i
    return None

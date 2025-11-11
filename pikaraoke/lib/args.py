import argparse
import logging
import os

from pikaraoke.lib.get_platform import get_default_dl_dir, get_platform, is_raspberry_pi


def arg_path_parse(path):
    if type(path) == list:
        return " ".join(path)
    else:
        return path


def parse_volume(volume, type):
    parsed_volume = float(volume)
    if parsed_volume > 1 or parsed_volume < 0:
        print(
            f"[ERROR] {type}: {volume} must be between 0 and 1. Setting to default: {default_volume}"
        )
        parsed_volume = default_volume
    return parsed_volume


# Default values for CLI args
platform = get_platform()
default_port = 5555
default_volume = 0.85
default_normalize_audio = False
default_splash_delay = 2
default_screensaver_delay = 300
default_log_level = logging.INFO
default_prefer_hostname = False
default_bg_music_volume = 0.3
default_buffer_size = 150
default_config_file_path = "config.ini"

default_dl_dir = get_default_dl_dir(platform)
default_youtubedl_path = "yt-dlp"


def parse_pikaraoke_args():
    # parse CLI args
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-p",
        "--port",
        help="Desired http port (default: %d)" % default_port,
        default=default_port,
        type=int,
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
        "--youtubedl-proxy",
        help="Proxy server to use for youtube-dl, in case blocked by a firewall",
        required=False,
    )
    parser.add_argument(
        "--ytdl-args",
        help="Additional arguments to pass to youtube-dl/yt-dlp (as a single string)",
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
        type=int,
        required=False,
    )
    parser.add_argument(
        "-t",
        "--screensaver-timeout",
        help="Delay before the screensaver begins (in secs). Set to 0 to disable screensaver. (default: %s )"
        % default_screensaver_delay,
        default=default_screensaver_delay,
        type=int,
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
        "--hide-overlay",
        action="store_true",
        help="Hide all overlays that show on top of video, including current/next song, pikaraoke QR code and IP",
        required=False,
    ),
    parser.add_argument(
        "--hide-notifications",
        action="store_true",
        help="Hide notifications from the splash screen.",
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
        help="Download higher quality video. May cause CPU, download speed, and other performance issues",
        required=False,
    )
    parser.add_argument(
        "-c",
        "--complete-transcode-before-play",
        action="store_true",
        help="Wait for ffmpeg video transcoding to fully complete before playback begins. Transcoding occurs when you have normalization on, play a cdg file, or change key. May improve performance and browser compatibility (Safari, Firefox), but will significantly increase the delay before playback begins. On modern hardware, the delay is likely negligible.",
        required=False,
    )
    parser.add_argument(
        "-b",
        "--buffer-size",
        help=f"Buffer size for transcoded video (in kilobytes). Increase if you experience songs cutting off early. Higher size will transcode more of the file before streaming it to the client. This will increase the delay before playback begins. This value is ignored if --complete-transcode-before-play was specified. Default is: {default_buffer_size}",
        default=default_buffer_size,
        type=int,
        required=False,
    ),
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
        "--window-size",
        help="Desired window geometry in pixels for headed mode, specified as width,height",
        default=0,
        required=False,
    )
    parser.add_argument(
        "--admin-password",
        help="Administrator password, for locking down certain features of the web UI such as queue editing, player controls, song editing, and system shutdown. If unspecified, everyone is an admin.",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "--disable-bg-music",
        action="store_true",
        help="Disable background music on splash screen",
        required=False,
    ),
    parser.add_argument(
        "--bg-music-volume",
        default=default_bg_music_volume,
        help="Set the volume of background music on splash screen. A value between 0 and 1. (default: %s)"
        % default_bg_music_volume,
        required=False,
    ),
    parser.add_argument(
        "--bg-music-path",
        nargs="+",
        help="Path to a custom directory for the splash screen background music. Directory must contain mp3 files which will be randomized in a playlist.",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "--bg-video-path",
        nargs="+",
        help="Path to a background video mp4 file. Will play in the background of the splash screen.",
        default=None,
        required=False,
    ),
    parser.add_argument(
        "--disable-bg-video",
        action="store_true",
        help="Disable background video on splash screen",
        required=False,
    ),
    parser.add_argument(
        "--disable-score",
        help="Disable the score screen after each song",
        action="store_true",
        required=False,
    ),
    parser.add_argument(
        "--limit-user-songs-by",
        help="Limit the number of songs a user can add to queue. User name 'Pikaraoke' is always unlimited (default: 0 = unlimited)",
        default="0",
        required=False,
    ),
    parser.add_argument(
        "--avsync",
        help="Use avsync (in seconds) if the audio and video streams are out of sync. (negative = advances audio | positive = delays audio)",
        default="0",
        required=False,
    ),
    parser.add_argument(
        "--config-file-path",
        help=f"Path to a config file to load settings from. Config file settings are set in the web interface or manually edited and will override command line arguments. Default {default_config_file_path}",
        default=default_config_file_path,
        required=False,
    ),
    parser.add_argument(
        "--cdg-pixel-scaling",
        help="Enable CDG pixel scaling to improve video rendering of CDG files. This may increase CPU usage and may cause performance issues on slower devices.",
        action="store_true",
        required=False,
    ),

    args = parser.parse_args()

    # additional sanitization of args:
    args.volume = parse_volume(args.volume, "Volume (default)")
    args.bg_music_volume = parse_volume(args.bg_music_volume, "Background Music Volume (default)")

    limit_user_songs_by = int(args.limit_user_songs_by)
    args.limit_user_songs_by = limit_user_songs_by

    youtubedl_path = arg_path_parse(args.youtubedl_path)
    logo_path = arg_path_parse(args.logo_path)
    bg_music_path = arg_path_parse(args.bg_music_path)
    bg_video_path = arg_path_parse(args.bg_video_path)

    if bg_video_path is not None and not os.path.isfile(bg_video_path):
        print(f"Background video not found: {bg_video_path}. Setting to None")

    dl_path = os.path.expanduser(arg_path_parse(args.download_path))
    if not dl_path.endswith("/"):
        dl_path += "/"

    args.youtubedl_path = youtubedl_path
    args.logo_path = logo_path
    args.bg_music_path = bg_music_path
    args.bg_video_path = bg_video_path
    args.download_path = dl_path

    return args

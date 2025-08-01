import logging
import subprocess

import ffmpeg


def get_media_duration(file_path):
    try:
        duration = ffmpeg.probe(file_path)["format"]["duration"]
        return round(float(duration))
    except:
        return None


def build_ffmpeg_cmd(
    fr,
    semitones=0,
    normalize_audio=True,
    buffer_fully_before_playback=False,
    avsync=0,
    cdg_pixel_scaling=False,
):
    avsync = float(avsync)
    # use h/w acceleration on pi
    default_vcodec = "h264_v4l2m2m" if supports_hardware_h264_encoding() else "libx264"
    # just copy the video stream if it's an mp4 or webm file, since they are supported natively in html5
    # otherwise use the default h264 codec
    vcodec = (
        "copy" if fr.file_extension == ".mp4" or fr.file_extension == ".webm" else default_vcodec
    )
    vbitrate = "5M"  # seems to yield best results w/ h264_v4l2m2m on pi, recommended for 720p.

    # copy the audio stream if no transposition/normalization, otherwise reincode with the aac codec
    is_transposed = semitones != 0
    acodec = "aac" if is_transposed or normalize_audio or avsync != 0 else "copy"

    input = ffmpeg.input(fr.file_path)
    audio = input.audio

    # If avsync is set, delay or trim the audio stream
    if avsync > 0:
        audio = audio.filter("adelay", f"{avsync * 1000}|{avsync * 1000}")  # delay
    elif avsync < 0:
        audio = audio.filter("atrim", start=-avsync)  # trim

    # The pitch value is (2^x/12), where x represents the number of semitones
    pitch = 2 ** (semitones / 12)

    audio = audio.filter("rubberband", pitch=pitch) if is_transposed else audio
    # normalize the audio
    audio = audio.filter("loudnorm", i=-16, tp=-1.5, lra=11) if normalize_audio else audio

    # frag_keyframe+default_base_moof is used to set the correct headers for streaming incomplete files,
    # without it, there's better compatibility for streaming on certain browsers like Firefox
    movflags = "+faststart" if buffer_fully_before_playback else "frag_keyframe+default_base_moof"

    if fr.cdg_file_path != None:  # handle CDG files
        logging.info("Playing CDG/MP3 file: " + fr.file_path)
        # copyts helps with sync issues, fps=25 prevents ffmpeg from needlessly encoding cdg at 300fps
        cdg_input = ffmpeg.input(fr.cdg_file_path, copyts=None)
        if cdg_pixel_scaling:
            video = cdg_input.video.filter("fps", fps=25).filter("scale", -1, 720, flags="neighbor")
        else:
            video = cdg_input.video.filter("fps", fps=25)

        # cdg is very fussy about these flags.
        # pi ffmpeg needs to encode to aac and cant just copy the mp3 stream
        # It also appears to have memory issues with hardware acceleration h264_v4l2m2m
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec="libx264",
            acodec="aac",
            preset="ultrafast",
            pix_fmt="yuv420p",
            listen=1,
            f="mp4",
            video_bitrate="500k",
            movflags=movflags,
        )
    else:
        video = input.video
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec=vcodec,
            acodec=acodec,
            preset="ultrafast",
            listen=1,
            f="mp4",
            video_bitrate=vbitrate,
            movflags=movflags,
        )

    args = output.get_args()
    logging.debug(f"COMMAND: ffmpeg " + " ".join(args))
    return output


def get_ffmpeg_version():
    try:
        # Execute the command 'ffmpeg -version'
        result = subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        # Parse the first line to get the version
        first_line = result.stdout.split("\n")[0]
        version_info = first_line.split(" ")[2]  # Assumes the version info is the third element
        return version_info
    except FileNotFoundError:
        return "FFmpeg is not installed"
    except IndexError:
        return "Unable to parse FFmpeg version"


def is_transpose_enabled():
    try:
        filters = subprocess.run(["ffmpeg", "-filters"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "rubberband" in filters.stdout.decode()


def supports_hardware_h264_encoding():
    try:
        codecs = subprocess.run(["ffmpeg", "-codecs"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "h264_v4l2m2m" in codecs.stdout.decode()


def is_ffmpeg_installed():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
    except FileNotFoundError:
        return False
    return True

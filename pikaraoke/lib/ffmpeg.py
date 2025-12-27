"""FFmpeg utilities for media processing and transcoding."""

import logging
import subprocess

import ffmpeg


def get_media_duration(file_path):
    """Get the duration of a media file in seconds.

    Args:
        file_path: Path to the media file.

    Returns:
        Duration in seconds (rounded), or None if unable to determine.
    """
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
    """Build an ffmpeg command for transcoding media.

    Handles video/audio codec selection, pitch shifting, audio normalization,
    and CDG file rendering.

    Args:
        fr: FileResolver instance with source file information.
        semitones: Number of semitones to shift pitch (0 = no shift).
        normalize_audio: Whether to apply loudness normalization.
        buffer_fully_before_playback: If True, use faststart for full buffering.
        avsync: Audio/video sync adjustment in seconds.
        cdg_pixel_scaling: Enable pixel scaling for CDG rendering.

    Returns:
        ffmpeg OutputStream object ready to execute.
    """
    avsync = float(avsync)
    # use h/w acceleration on pi
    using_hardware_encoder = supports_hardware_h264_encoding()
    default_vcodec = "h264_v4l2m2m" if using_hardware_encoder else "libx264"
    # just copy the video stream if it's an mp4 file (already H.264 compatible)
    # webm uses VP8/VP9 which must be transcoded to H.264 for fMP4 containers
    vcodec = "copy" if fr.file_extension == ".mp4" else default_vcodec

    # Optimize bitrate for Raspberry Pi hardware encoder
    # Pi 3B+ struggles with 5M bitrate in real-time, 2M provides better stability
    # while maintaining good quality for karaoke videos
    if using_hardware_encoder:
        vbitrate = "2M"  # Optimized for Pi h264_v4l2m2m real-time encoding
    else:
        vbitrate = "5M"  # Higher quality for more powerful hardware

    # copy the audio stream if no transposition/normalization, otherwise reincode with the aac codec
    is_transposed = semitones != 0
    acodec = "aac" if is_transposed or normalize_audio or avsync != 0 else "copy"

    # For container formats that may have VFR or timestamp issues, use genpts
    # This fixes playback issues with AVI, MOV, MKV, and WEBM when streaming
    if fr.file_extension in [".webm", ".avi", ".mov", ".mkv"]:
        input = ffmpeg.input(fr.file_path, **{"fflags": "+genpts"})
    else:
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

    if fr.cdg_file_path != None:  # handle CDG files
        logging.info("Playing CDG/MP3 file: " + fr.file_path)
        # copyts helps with sync issues, fps=25 prevents ffmpeg from needlessly encoding cdg at 300fps
        cdg_input = ffmpeg.input(fr.cdg_file_path, copyts=None)
        if cdg_pixel_scaling:
            video = cdg_input.video.filter("fps", fps=25).filter("scale", -1, 720, flags="neighbor")
        else:
            video = cdg_input.video.filter("fps", fps=25)

        # Both MP4 and HLS modes use HLS format (init.mp4 + fMP4 segments)
        # The difference is in how they're served:
        # - mp4: Stream concatenates init + segments for progressive playback
        # - hls: Browser requests segments via .m3u8 playlist
        #
        # This approach works because:
        # - Segments are pre-encoded (no real-time streaming pressure)
        # - Hardware encoding (h264_v4l2m2m) works reliably with discrete segments
        # - Both Chrome and Smart TVs can use the same encoded output
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec="libx264",
            acodec="aac",
            audio_bitrate="192k",  # Explicit quality for AAC
            ac=2,  # Force stereo (downmix surround sound)
            ar=48000,  # Standard sample rate for streaming
            preset="ultrafast",
            pix_fmt="yuv420p",
            f="hls",
            hls_time=3,
            hls_list_size=0,
            hls_segment_type="fmp4",
            hls_fmp4_init_filename=fr.init_filename,
            hls_segment_filename=fr.segment_pattern,
            video_bitrate="500k",
            **{"vsync": "cfr", "avoid_negative_ts": "make_zero"},  # Force constant frame rate and fix negative timestamps
        )
    else:
        video = input.video

        # For WEBM files, genpts at input level handles timestamp regeneration
        # No additional filters needed - let genpts + avoid_negative_ts do the work

        # Both MP4 and HLS modes use HLS format (init.mp4 + fMP4 segments)
        # The difference is in how they're served:
        # - mp4: Stream concatenates init + segments for progressive playback
        # - hls: Browser requests segments via .m3u8 playlist
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec=vcodec,
            acodec="aac",  # Force AAC encoding for compatibility
            audio_bitrate="192k",  # Explicit quality for AAC
            ac=2,  # Force stereo (downmix surround sound)
            ar=48000,  # Standard sample rate for streaming
            preset="ultrafast",
            f="hls",
            hls_time=3,
            hls_list_size=0,
            hls_segment_type="fmp4",
            hls_fmp4_init_filename=fr.init_filename,
            hls_segment_filename=fr.segment_pattern,
            video_bitrate=vbitrate,
            **{"vsync": "cfr", "avoid_negative_ts": "make_zero"},  # Force constant frame rate and fix negative timestamps
        )

    args = output.get_args()
    logging.debug(f"COMMAND: ffmpeg " + " ".join(args))
    return output


def get_ffmpeg_version():
    """Get the installed FFmpeg version string.

    Returns:
        Version string, or an error message if FFmpeg is not installed
        or version cannot be parsed.
    """
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
    """Check if FFmpeg has the rubberband filter for pitch shifting.

    Returns:
        True if rubberband filter is available, False otherwise.
    """
    try:
        filters = subprocess.run(["ffmpeg", "-filters"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "rubberband" in filters.stdout.decode()


def supports_hardware_h264_encoding():
    """Check if hardware H.264 encoding (h264_v4l2m2m) is available.

    Returns:
        True if hardware encoding is available, False otherwise.
    """
    try:
        codecs = subprocess.run(["ffmpeg", "-codecs"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "h264_v4l2m2m" in codecs.stdout.decode()


def is_ffmpeg_installed():
    """Check if FFmpeg is installed and accessible.

    Returns:
        True if FFmpeg is installed, False otherwise.
    """
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
    except FileNotFoundError:
        return False
    return True

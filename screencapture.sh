#!/bin/bash
#
# USAGE: screencapture.sh [OPTIONS]
# DESCRIPTION: Capture screen and audio with ffmpeg and share it via a very primitive HTTP server.
# OPTIONS: See 'screencapture.sh --help'
#

# Default settings

PORT="8080"

DISPLAYNAME=":0.0"
CAPTUREORIGIN="0,0"
CAPTURESIZE=$(xwininfo -display "$DISPLAYNAME" -root|grep --extended-regexp --only-matching "\-geometry\s[0-9]+x[0-9]+"|cut -d " " -f 2)

SOUNDSERVER="alsa"
AUDIODEVICE="default"
AUDIODELAY="0.16"

VIDEOSCALE="1"

TARGETBITRATE="4M"
MAXBITRATE="6M"
BUFFERSIZE="12M"

FRAMERATE="30"
SEGMENTDURATION="2"

MAXSEGMENTS="4"

TEMPDIRPARENT=""

LOGLEVEL="quiet"

# Parse command line arguments

while [ -n "$1" -a "$1" != "--" ]
do
	case "$1" in
		-p|--port)
			PORT="$2"
			args=1
			;;
		-n|--displayname)
			DISPLAYNAME="$2"
			args=1
			;;
		-o|--captureorigin)
			CAPTUREORIGIN="$2"
			args=1
			;;
		-c|--capturesize)
			CAPTURESIZE="$2"
			args=1
			;;
		-s|--soundserver)
			SOUNDSERVER="$2"
			args=1
			;;
		-a|--audiodevice)
			AUDIODEVICE="$2"
			args=1
			;;
		-d|--audiodelay)
			AUDIODELAY="$2"
			args=1
			;;
		-e|--videoscale)
			VIDEOSCALE="$2"
			args=1
			;;
		-f|--framerate)
			FRAMERATE="$2"
			args=1
			;;
		-b|--targetbitrate)
			TARGETBITRATE="$2"
			args=1
			;;
		-m|--maxbitrate)
			MAXBITRATE="$2"
			args=1
			;;
		-B|--buffersize)
			BUFFERSIZE="$2"
			args=1
			;;
		-D|--segmentduration)
			SEGMENTDURATION="$2"
			args=1
			;;
		-M|--maxsegments)
			MAXSEGMENTS="$2"
			args=1
			;;
		-t|--tempdir)
			TEMPDIRPARENT="$2"
			args=1
			;;
		-v|--verbose)
			LOGLEVEL="verbose"
			args=0
			;;
		-h|-\?|--help)
			cat <<-"EOF"
			Capture screen and audio with ffmpeg and share it via a very primitive HTTP server.
			Usage: screencapture.sh [OPTIONS]
			Options:
			  -p, --port port                  Valid source port number for HTTP server
			  -n, --displayname name           Display name in the form hostname:displaynumber.screennumber
			  -o, --captureorigin position     Capture origin in the form X,Y (e.g. 208,254)
			  -c, --capturesize dimensions     Capture size in the form WxH (e.g. 640x480)
			  -s, --soundserver name           Sound server to use ("alsa", "pulse", "oss", ...)
			  -a, --audiodevice device         Audio input device
			  -d, --audiodelay seconds         Sound delay in seconds (e.g. 0.22)
			  -e, --videoscale scale           Output image scale factor (e.g. 0.75)
			  -f, --framerate number           Output video frames per second (whole number)
			  -b, --targetbitrate size         Output video target bitrate (e.g. 3M)
			  -m, --maxbitrate size            Output video maximum bitrate (e.g. 4M)
			  -B, --buffersize size            Video bitrate controler buffer size (e.g. 8M)
			  -D, --segmentduration seconds    Duration of each segment file (in whole seconds)
			  -M, --maxsegments number         Maximum amount of old files kept for each stream (audio and video)
			  -t, --tempdir directory          Custom directory for temporary files
			  -v, --verbose                    Show ffmpeg's verbose output
			  -?, --help                       Print this help
			EOF
			exit 0
			;;
		*)
			echo "Invalid option:  $1" >&2
			exit 1
			;;
	esac

	if ! shift "$((1+$args))"
	then
		echo "Option $1 needs $args argument" >&2
		exit 1
	fi
done

startCapture(){
	echo -n "\"use strict\";var mimeCodec=[\"video/mp4; codecs=\\\"avc1.42c01f\\\"\",\"audio/mp4; codecs=\\\"mp4a.40.2\\\"\"];" > metadata.js

	mkdir 0 1

	ffmpeg \
		-loglevel "$LOGLEVEL" \
		-f x11grab -framerate "$FRAMERATE" -s:size "$CAPTURESIZE" -thread_queue_size 64 -i "$DISPLAYNAME+$CAPTUREORIGIN" \
		-f "$SOUNDSERVER" -thread_queue_size 1024 -itsoffset "$AUDIODELAY" -i "$AUDIODEVICE" \
		-pix_fmt yuv420p \
		-filter:a "aresample=first_pts=0" \
		-c:a aac -strict experimental -b:a 128k -ar 48000 \
		-filter:v "scale=trunc(iw*$VIDEOSCALE/2)*2:trunc(ih*$VIDEOSCALE/2)*2" \
		-c:v libx264 -profile:v baseline -tune fastdecode -preset ultrafast -b:v "$TARGETBITRATE" -maxrate "$MAXBITRATE" -bufsize "$BUFFERSIZE" -r "$FRAMERATE" -g "$(($FRAMERATE*$SEGMENTDURATION))" -keyint_min "$(($FRAMERATE*$SEGMENTDURATION))" \
		-movflags +empty_moov+frag_keyframe+default_base_moof+cgop \
		-f dash -min_seg_duration "$SEGMENTDURATION"000000 -use_template 0 -window_size "$MAXSEGMENTS" -extra_window_size 0 -remove_at_exit 1 -init_seg_name "\$RepresentationID\$/0" -media_seg_name "\$RepresentationID\$/\$Number\$" manifest.mpd

	local status="$?"
	if [ "$status" != "0" -a "$status" != "255" ]
	then
		if [ -d "$TEMPDIR" ]
		then
			echo -n "ffmpeg exited with nonzero status $status." >&2
			if [ "$LOGLEVEL" == "quiet" ]
			then
				echo " Use -v to show more information." >&2
			else
				echo >&2
			fi
		fi

		killMainProcess "$status"
	else
		killMainProcess 0
	fi
}

startServer(){
	cat >server.sh <<-"EOFF"
	#!/bin/bash

	read -r -d "" HTML <<-"EOF"
	<!DOCTYPE html>
	<html>
	<head>
		<meta charset="UTF-8">
		<title>Screen</title>
		<style>
			body{
				margin:0;
				overflow:hidden;
				background:#000;
			}
			video{
				height:100vh;
				width:100vw;
			}
		</style>
		<script src="/metadata.js"></script>
		<script>
			"use strict";

			var nextId = [];
			var sourceBuffer = [];
			var httpRequest = [];
			var queuedAppend = [];
			var mediaSource;
			var videoElement;

			var autoplayMessage = true;
			var mutedMessage = true;

			// restarts stream i at nextId[i]
			function abortAndRestart(i) {
				if(httpRequest[i] != null) {
					httpRequest[i].abort();
					httpRequest[i] = null;
				}

				queuedAppend[i] = null;

				var sb = sourceBuffer[i];
				sb.abort();

				if(sb.buffered.length > 0) {
					sb.remove(0, sb.buffered.end(sb.buffered.length - 1));
				}
				else {
					fetchNext(i);
				}
			}

			function getArrayBuffer(url, callback, e404Callback) {
				var xhr = new XMLHttpRequest();

				xhr.addEventListener("load", function() {
					if(xhr.status == 404) {
						e404Callback(parseInt(xhr.getResponseHeader("Next-Segment")));
					}
					else {
						callback(xhr.response);
					}
				}, false);

				xhr.addEventListener("error", function() {
					setTimeout(function() {
						var id = httpRequest.indexOf(xhr);
						if(id != -1) { // request was not aborted
							httpRequest[id] = getArrayBuffer(url, callback, e404Callback);
						}
					}, 1000);
				}, false);

				xhr.open("GET", url);
				xhr.responseType = "arraybuffer";
				xhr.send();

				return xhr;
			}

			// add ArrayBuffer buf to sourceBuffer[i]
			function sourceBufferAppend(i, buf) {
				var sb = sourceBuffer[i];

				try {
					sb.appendBuffer(buf);
				}
				catch(e) {
					if(sb.buffered.length > 0) { // e is QuotaExceededError
						queuedAppend[i] = buf;

						var start = sb.buffered.start(0);
						var end = sb.buffered.end(sb.buffered.length - 1);

						sb.remove(start, Math.max(end - 60, (start + end) / 2)); // remove old frames that were not automatically evicted by the browser
					}
					else {
						abortAndRestart(i);
					}
				}
			}

			function tryToPlay() {
				var p = videoElement.play();
				if(p && autoplayMessage) {
					p.catch(function() {
						if(autoplayMessage) {
							autoplayMessage = false;
							alert("Autoplay is disabled. Click on the video to start it.");
						}
					});
				}

				if(videoElement.muted && mutedMessage) {
					mutedMessage = false;
					alert("Sound is muted. Click on the video to unmute it.");
				}
			}

			// set currentTime to a valid position and play video
			function tryToPlayAndAjustTime() {
				if(videoElement.buffered.length > 0) {
					var start = videoElement.buffered.start(videoElement.buffered.length - 1);

					if(videoElement.currentTime <= start) {
						videoElement.currentTime = start;
						if(videoElement.paused) {
							tryToPlay();
						}
					}
					else {
						var end = videoElement.buffered.end(videoElement.buffered.length - 1);

						if(videoElement.currentTime > end) {
							videoElement.currentTime = end;
							if(videoElement.paused) {
								tryToPlay();
							}
						}
					}
				}
			}

			function addUpdateendListener(i) {
				sourceBuffer[i].addEventListener("updateend", function() { // this is executed when the SourceBuffer.appendBuffer or SourceBuffer.remove has ended
					var ab = queuedAppend[i];

					if(ab == null) {
						fetchNext(i);
						tryToPlayAndAjustTime();
					}
					else { // previous append failed in sourceBufferAppend(i, buf) and ab needs to be added again
						queuedAppend[i] = null;
						sourceBufferAppend(i, ab);
					}
				}, false);
			}

			function fetchNext(streamId) {
				var segmentId = nextId[streamId];
				httpRequest[streamId] = getArrayBuffer("/" + streamId + "/" + segmentId, function(buf) {
					httpRequest[streamId] = null;
					nextId[streamId]++;
					sourceBufferAppend(streamId, buf);
				}, function(nextSegment) {
					httpRequest[streamId] = null;
					nextId[streamId] = nextSegment;
					abortAndRestart(streamId);
				});
			}

			window.addEventListener("load", function() {
				if(!("MediaSource" in window)) {
					alert("MediaSource API not supported.");
				}
				else {
					for(var i = 0; i < mimeCodec.length; i++) {
						if(!MediaSource.isTypeSupported(mimeCodec[i])) {
							alert("Unsupported media type or codec: " + mimeCodec[i]);
						}

						nextId.push(0);
						sourceBuffer.push(null);
						httpRequest.push(null);
						queuedAppend.push(null);
					}

					mediaSource = new MediaSource();
					mediaSource.addEventListener("sourceopen", function() {
						for(var i = 0; i < mimeCodec.length; i++) {
							sourceBuffer[i] = mediaSource.addSourceBuffer(mimeCodec[i]);

							if(!sourceBuffer[i].updating) {
								fetchNext(i);
							}

							addUpdateendListener(i);
						}
					}, false);

					videoElement = document.querySelector("video#v");
					videoElement.src = URL.createObjectURL(mediaSource);

					videoElement.addEventListener("click", function(e) {
						if(videoElement.muted) {
							videoElement.muted = false;
						}
						if(videoElement.paused) {
							videoElement.play();
						}
						e.preventDefault();
					}, false);
				}
			}, false);
		</script>
	</head>
	<body>
		<video id="v" muted></video>
	</body>
	</html>
	EOF

	getLastSegmentId(){
		local streamId="$1"
		if [ -f "$streamId/0" ]
		then
			local files=$(ls -xt --ignore "*[^0-9]*" "$streamId/" 2>/dev/null)
			echo -n ${files%%[\. ]*}
		else
			echo -n 0
		fi
	}

	printOtherHeaders(){
		echo -ne "Connection: keep-alive\r\nCache-Control: no-cache, no-store, must-revalidate\r\nPragma: no-cache\r\nExpires: 0\r\nServer: screencapture\r\n\r\n"
	}

	printHeaders200(){
		echo -ne "HTTP/1.1 200 OK\r\nContent-Type: $1\r\nContent-Length: $2\r\n"
		printOtherHeaders
	}

	printHeaders404(){
		local lastSegmentId="$1"
		if [ -z "$lastSegmentId" ]
		then
			lastSegmentId=0
		fi
		echo -ne "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nNext-Segment: $lastSegmentId\r\n"
		printOtherHeaders
	}

	printMetaData(){
		printHeaders200 text/javascript "$(stat --printf="%s" metadata.js)"
		cat metadata.js
	}

	printHTML(){
		printHeaders200 text/html "${#HTML}"
		echo -n "$HTML"
	}

	sleepABit(){
		if type usleep >/dev/null 2>&1
		then
			usleep 200000
		else
			sleep 0.2
		fi
	}

	waitFileExistence(){
		until [ -f "$1" ]
		do
			sleepABit
		done
	}

	waitFileNotEmpty(){
		until [ -s "$1" ]
		do
			sleepABit
		done
	}

	printSegmentResponse(){
		local streamId="${1%%/*}"

		if [ ! -d "$streamId" ]
		then
			printHeaders404
			return
		fi

		local segmentId="${1##*/}"

		if [ "$segmentId" == "0" ]
		then
			waitFileExistence "$1"
		elif [ ! -f "$1" ]
		then
			local lastSegmentId=$(getLastSegmentId "$streamId")
			if [ "$segmentId" -ge "$lastSegmentId" ] && [ "$segmentId" -lt "$((3+$lastSegmentId))" ]
			then
				waitFileExistence "$1"
			else
				printHeaders404 "$lastSegmentId"
				return
			fi
		fi

		waitFileNotEmpty "$1"

		local size=$(stat --printf="%s" "$1" 2>/dev/null)

		if [ "$?" == "0" ]
		then
			{
				exec 3<"$1"
			} 2>/dev/null

			if [ "$?" == "0" ]
			then
				printHeaders200 application/octet-stream "$size"
				cat <&3
				exec 3<&-
			else
				printHeaders404 "$lastSegmentId"
			fi
		else
			printHeaders404 "$lastSegmentId"
		fi
	}

	shopt -s extglob

	CR=$(echo -ne "\r")

	while read -r input
	do
		if [[ "$input" = GET[[:space:]]/*([[:ascii:]])[[:space:]]HTTP/1\.[01]?($CR) ]]
		then
			input="${input% *}"
			input="${input#GET /}"
			case "$input" in
				+([0-9])/+([0-9]))
					printSegmentResponse "$input"
					;;
				"")
					printHTML
					;;
				metadata\.js)
					printMetaData
					;;
				*)
					printHeaders404
					;;
			esac
		fi
	done
	EOFF

	chmod +x server.sh

	if type ncat >/dev/null 2>&1
	then
		ncat --listen --keep-open --source-port "$PORT" --exec ./server.sh
	elif type socat >/dev/null 2>&1
	then
		socat TCP-LISTEN:"$PORT",fork EXEC:./server.sh
	elif type tcpserver >/dev/null 2>&1
	then
		tcpserver 0.0.0.0 "$PORT" ./server.sh
	elif type socket >/dev/null 2>&1
	then
		socket -f -p ./server.sh -s -l "$PORT"
	else
		echo "None of those TCP/IP swiss army knives installed on this system: ncat, socat, tcpserver, socket." >&2
		false # set $? to 1
	fi

	killMainProcess "$?"
}

killMainProcess(){
	{
		echo "$1" >> exitcode
		kill -s INT "$$"
	} 2>/dev/null
}

killChildProcesses(){
	for pid in $(ps -o pid= --ppid "$1")
	do
		kill -s TSTP "$pid" 2>/dev/null
		killChildProcesses "$pid"
		kill -s INT "$pid" 2>/dev/null
	done
}

exitTrap(){
	trap "" INT QUIT TERM EXIT
	killChildProcesses "$$"
	if [ -f exitcode ]
	then
		local code=$(head --lines=1 exitcode)
	fi
	cd "$OLDDIR"
	rm --recursive --force "$TEMPDIR"
	if [ -n "$code" ]
	then
		exit "$code"
	fi
}

TEMPDIR=$(mktemp --directory --suffix=ffmpeg-screen-capture --tmpdir="$TEMPDIRPARENT")
if [ "$?" != "0" ]
then
	exit "$?"
fi

OLDDIR=$(pwd)
cd "$TEMPDIR"

trap "exitTrap" INT QUIT TERM EXIT

startCapture&
startServer&

wait

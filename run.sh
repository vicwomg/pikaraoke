#!/usr/bin/env bash

session_name=PiKaraoke
cmds=("top"
#"sudo sh -c 'cp -f $HOME/.Xauthority ~ && $HOME/anaconda3/bin/python3 app.py -u $(whoami)'"
"$HOME/anaconda3/bin/python3 app.py"
"./screencapture.sh -v -D 1 -e 1 -p 4000"
"# pavucontrol"
)

if [ "`tmux ls | grep $session_name`" ]; then
	echo "TMUX Session $session_name already exists!" >&2
	exit 1
fi

export DISPLAY=:0

cd "`dirname $0`"
tmux new-session -s $session_name -d -x 240 -y 60

for i in `seq 0 $[${#cmds[*]}-1]`; do
	sleep 0.2
	tmux split-window
	sleep 0.2
	tmux select-layout tile
	sleep 0.2
	tmux send-keys -l "${cmds[i]}"
	sleep 0.2
	tmux send-keys Enter
done

# Set pulseaudio recording source
src="`pacmd list-sources | grep '.monitor>' | awk '{print $2}' | head -1 `"
idx="`pacmd list-source-outputs | grep index: | awk '{print $2}' | tail -1`"
if [ "$src" ]; then
	pacmd move-source-output $idx "${src:1:-1}"
fi

tmux a -t $session_name


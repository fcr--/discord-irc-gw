#!/bin/bash

#exit
trap "kill \`pgrep -P $$\` 2>/dev/null" TERM

youtube-dl -f 'bestaudio[ext=m4a]' --no-playlist -4 -q -o - --no-part -- "$1" 2>/dev/null | \
    mbuffer -b 100000 -q -s 1kB 2>/dev/null | mplayer -quiet - >/dev/null 2>&1 &

wait %1
true

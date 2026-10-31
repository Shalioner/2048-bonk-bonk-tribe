#!/bin/zsh
cd "$(dirname "$0")" || exit 1
export PATH=/opt/homebrew/bin:$PATH
rm -f STOP
python3 bot.py --steps 10000 "$@" 2>&1 | tee -a play.log
read "?Нажмите Enter для закрытия"

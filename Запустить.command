#!/bin/zsh
cd "$(dirname "$0")" || exit 1
export PATH=/opt/homebrew/bin:$PATH
python3 bot.py --clear-stop --steps 10000 --early-think 3 --wait-energy "$@" 2>&1 | tee -a play.log
read "?Нажмите Enter для закрытия"

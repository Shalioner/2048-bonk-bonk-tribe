#!/bin/sh
# Builds the search engine next to this script: engine.dylib on macOS, engine.so on Linux.
set -eu
cd "$(dirname "$0")"
if [ "$(uname -s)" = "Darwin" ]; then
    clang++ -O3 -std=c++11 -dynamiclib engine_adapted.cpp -o engine.dylib
else
    g++ -O3 -std=c++11 -shared -fPIC -pthread engine_adapted.cpp -o engine.so
fi

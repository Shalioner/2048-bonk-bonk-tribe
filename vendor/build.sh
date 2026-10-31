#!/bin/sh
set -eu
cd "$(dirname "$0")"
clang++ -O3 -std=c++11 -dynamiclib engine_adapted.cpp -o engine.dylib

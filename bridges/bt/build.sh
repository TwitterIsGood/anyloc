#!/bin/zsh
set -e
cd "$(dirname "$0")"
clang++ -arch x86_64 -std=c++17 -O2 -Wall -Wextra \
  -o anyloc-bt-bridge anyloc_bt_bridge.cpp -ldl

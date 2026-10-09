#!/usr/bin/env bash
# 11n and 26n side by side, then 26s alone.
cd /data/curling/stone
./train.sh /data/curling/stone/weights/yolo11n.pt ds17b-11n &
./train.sh /data/curling/stone/weights/yolo26n.pt ds17b-26n &
wait
./train.sh yolo26s.pt ds17b-26s
echo "$(date +%T) all done" >> /data/curling/stone/progress.txt

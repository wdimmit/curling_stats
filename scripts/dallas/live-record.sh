#!/bin/sh
# Raw recording of the DFW live stream 46wEMds_tYQ (user asked to record it to reprocess as needed).
# As the worker records: from the first segment (-live_start_index 0), so the file is on the
# video own clock; MPEG-TS so it is readable while it grows; a new file after a drop.
cd /w/live2
n=0
while [ ! -f STOP ]; do
  yt-dlp --extractor-args "youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416" --wait-for-video 15     -f "bv*[height<=1080][vcodec^=avc1]/bv*[height<=1080]/bv*" --hls-use-mpegts --no-part --newline     --downloader-args "ffmpeg_i:-live_start_index 0" -o "raw-$n.ts"     "https://www.youtube.com/watch?v=46wEMds_tYQ" >> record.log 2>&1
  echo "$(date) yt-dlp exited $?" >> record.log
  n=$((n+1))
  sleep 5
done

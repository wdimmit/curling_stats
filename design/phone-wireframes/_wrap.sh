#!/bin/bash
# Assemble a .dc.html artboard: shared chrome CSS + this artboard's body.
# Each .dc.html is written whole, so it stands alone as a Design Component.
name="$1"; h="${2:-844}"
{
  printf '%s\n' '<!doctype html>' '<html>' '<head>' '  <meta charset="utf-8">' \
    '  <script src="./support.js"></script>' '</head>' '<body>' '<x-dc>' '<helmet>' '  <style>'
  sed 's/^/    /' _shared.css
  sed 's/^/    /' "_extra_$name.css" 2>/dev/null
  printf '%s\n' '  </style>' '</helmet>'
  cat "_body_$name.html"
  printf '%s\n' '</x-dc>' \
    "<script data-dc-script data-props='{\"\$preview\":{\"width\":390,\"height\":$h}}'>" \
    'class Component extends DCLogic {}' '</script>' '</body>' '</html>'
} > "$name.dc.html"
echo "$name.dc.html  $(wc -l < "$name.dc.html") lines"

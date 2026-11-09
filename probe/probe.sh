#!/usr/bin/env bash
UA="Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
while IFS='|' read -r name url; do
  code=$(curl -s -L -m 25 -A "$UA" -o /tmp/r -w "%{http_code}" "$url")
  n=$(grep -o -i -E "<item[ >]|<entry[ >]|\"doi\":" /tmp/r | wc -l)
  t=$(grep -o -E "<title[^>]*>[^<]{0,80}" /tmp/r | sed -n 2p | sed 's/<title[^>]*>//')
  [ -z "$t" ] && t=$(head -c 100 /tmp/r | tr '\n' ' ')
  echo "$code n=$n $name :: $t"
done < probe/urls.txt

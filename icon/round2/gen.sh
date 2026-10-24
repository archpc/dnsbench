set -e
mkdir -p lanes_dark lanes_light sonar_dark sonar_light cards_dark cards_light trace_dark trace_light
# --- lanes ---
for p in dark light; do
  if [ $p = dark ]; then BG1="#20293A"; BG2="#131A26"; N1="#C8D2E0"; N2="#8A96A8"; ACC="#F5A623"
  else BG1="#F2F0EA"; BG2="#F2F0EA"; N1="#232F49"; N2="#9AA3B2"; ACC="#E85D3F"; fi
  cat > lanes_$p/1_background.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="$BG1"/><stop offset="1" stop-color="$BG2"/></linearGradient></defs><rect width="1024" height="1024" fill="url(#g)"/></svg>
EOB
  cat > lanes_$p/2_foreground.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<rect x="200" y="332" width="560" height="96" rx="48" fill="$ACC"/>
<rect x="200" y="464" width="420" height="96" rx="48" fill="$N1"/>
<rect x="200" y="596" width="340" height="96" rx="48" fill="$N2"/>
</svg>
EOB
done
# --- sonar ---
for p in dark light; do
  if [ $p = dark ]; then BG1="#20293A"; BG2="#131A26"; R1="#9FB2CC"; R2="#C9D6E8"; DOT="#F5A623"; BLIP="#E8EDF5"
  else BG1="#F2F0EA"; BG2="#F2F0EA"; R1="#8A97AD"; R2="#5F6E86"; DOT="#E85D3F"; BLIP="#232F49"; fi
  cp /dev/null sonar_$p/1_background.svg
  cat > sonar_$p/1_background.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="$BG1"/><stop offset="1" stop-color="$BG2"/></linearGradient></defs><rect width="1024" height="1024" fill="url(#g)"/></svg>
EOB
  cat > sonar_$p/2_foreground.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<circle cx="512" cy="512" r="392" fill="none" stroke="$R1" stroke-width="36" opacity="0.5"/>
<circle cx="512" cy="512" r="252" fill="none" stroke="$R2" stroke-width="52" opacity="0.92"/>
<circle cx="777" cy="223" r="64" fill="$BLIP"/>
<circle cx="512" cy="512" r="88" fill="$DOT"/>
</svg>
EOB
done
# --- cards ---
for p in dark light; do
  if [ $p = dark ]; then BG1="#20293A"; BG2="#131A26"; BACK="#5C6878"; FRONT="#E8EDF5"; DOT="#F5A623"
  else BG1="#F2F0EA"; BG2="#F2F0EA"; BACK="#C6CCD6"; FRONT="#232F49"; DOT="#E85D3F"; fi
  cat > cards_$p/1_background.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="$BG1"/><stop offset="1" stop-color="$BG2"/></linearGradient></defs><rect width="1024" height="1024" fill="url(#g)"/></svg>
EOB
  cat > cards_$p/2_foreground.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<rect x="380" y="236" width="380" height="290" rx="44" fill="$BACK"/>
<rect x="264" y="380" width="380" height="290" rx="44" fill="$FRONT"/>
<circle cx="564" cy="525" r="56" fill="$DOT"/>
</svg>
EOB
done
# --- trace ---
for p in dark light; do
  if [ $p = dark ]; then BG1="#20293A"; BG2="#131A26"; LANE="#C8D2E0"; GHOST="#5C6878"; DOT="#F5A623"
  else BG1="#F2F0EA"; BG2="#F2F0EA"; LANE="#232F49"; GHOST="#9AA3B2"; DOT="#E85D3F"; fi
  cat > trace_$p/1_background.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="$BG1"/><stop offset="1" stop-color="$BG2"/></linearGradient></defs><rect width="1024" height="1024" fill="url(#g)"/></svg>
EOB
  cat > trace_$p/2_foreground.svg <<EOB
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<rect x="170" y="470" width="580" height="88" rx="44" fill="$LANE"/>
<circle cx="452" cy="514" r="44" fill="$GHOST"/>
<circle cx="790" cy="514" r="76" fill="$DOT"/>
</svg>
EOB
done
echo generated

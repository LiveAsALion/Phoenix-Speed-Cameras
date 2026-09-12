# Usage: python3 crop_moment.py <full-card image> <out dir>
# Cuts the illustration slot (below the sub-line, above the Continue button)
# out of a full-card mockup, feathers the edges to transparent, and writes
# moment.png + moment.webp at 1179 px wide (3x of the 393 pt body).
import sys, os
from PIL import Image
import numpy as np
src, out = sys.argv[1], sys.argv[2]
im = Image.open(src).convert('RGB'); a = np.asarray(im).astype(int); h, w, _ = a.shape
# CTA blue is (≈47,125,240): red low, green mid, blue high. The sonar arcs are
# cyan (green high) and never span 70% of the width, so they cannot match.
cta = ((a[:, :, 0] < 120) & (a[:, :, 1] > 80) & (a[:, :, 1] < 170) & (a[:, :, 2] > 200)).sum(axis=1)
btn_rows = [y for y in range(int(h * 0.7), h) if cta[y] > w * 0.7]
if not btn_rows: raise SystemExit("no Continue button found")
btn_top = min(btn_rows)
# Anchor on the red camera pin first (the first wide red band in the middle
# of the card), then take the last light text row above it as the sub-line.
redc = ((a[:, :, 0] > 150) & (a[:, :, 1] < 100) & (a[:, :, 2] < 100)).sum(axis=1)
red = [y for y in range(int(h * 0.25), int(h * 0.6)) if redc[y] > 20]
pin_top = min(red)
light = ((a[:, :, 0] > 140) & (a[:, :, 1] > 140) & (a[:, :, 2] > 140)).sum(axis=1)
text = [y for y in range(int(h * 0.18), pin_top - int(h * 0.01)) if light[y] > 40]
sub_bottom = max(text)
top = sub_bottom + (pin_top - sub_bottom) // 2
bottom = btn_top - int(h * 0.012)
print(f"size={w}x{h} sub_bottom={sub_bottom} pin_top={pin_top} button_top={btn_top} -> crop y {top}..{bottom}")
crop = im.crop((0, top, w, bottom)); cw, ch = crop.size
alpha = np.full((ch, cw), 255.0); fy = max(60, ch // 10); fx = max(60, cw // 12)
ry = np.linspace(0, 1, fy); rx = np.linspace(0, 1, fx)
alpha[:fy, :] *= ry[:, None]; alpha[-fy:, :] *= ry[::-1][:, None]
alpha[:, :fx] *= rx[None, :]; alpha[:, -fx:] *= rx[::-1][None, :]
rgba = Image.merge('RGBA', [*crop.split(), Image.fromarray(alpha.astype(np.uint8))])
scale = 1179 / cw; rgba = rgba.resize((1179, int(ch * scale)), Image.LANCZOS)
os.makedirs(out, exist_ok=True)
rgba.save(f"{out}/moment.png", optimize=True); rgba.save(f"{out}/moment.webp", quality=90, method=6)
edge = np.concatenate([a[top:top+20].reshape(-1, 3), a[bottom-20:bottom].reshape(-1, 3), a[top:bottom, :30].reshape(-1, 3), a[top:bottom, -30:].reshape(-1, 3)]).mean(axis=0)
print("final", rgba.size, "png", os.path.getsize(f"{out}/moment.png") // 1024, "KB webp", os.path.getsize(f"{out}/moment.webp") // 1024, "KB  edge bg #%02x%02x%02x" % tuple(int(v) for v in edge))

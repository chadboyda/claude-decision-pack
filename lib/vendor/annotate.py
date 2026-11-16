#!/usr/bin/env python3
"""Burn numbered callouts into screenshots and build before/after sheets.

mark: draw numbered boxes (and an optional legend) on one screenshot.
  python3 annotate.py mark IN.png CALLOUTS.json OUT.png [--legend] [--color "#E5484D"]
  CALLOUTS.json: [{"n": 1, "box": [x, y, w, h], "note": "Price blends into the list"}]
  Box coordinates are page CSS px. shoot.py/probe.py shoot at device scale 1, so CSS px == image px.
  For a screenshot taken at another devicePixelRatio (shoot.py --dpr 2, Chrome on a Retina screen),
  pass --scale <devicePixelRatio> and the boxes are multiplied to match. The viewport shot covers
  only page y 0..844 (390) or 0..900 (1440): mark anything lower on <name>-W-full.png. A box mostly
  outside the image is an error (non-zero exit), not a badge clamped onto unrelated content. For a shot
  taken after scrolling, pass --offset 0 <scrollY> (shoot.py prints it).
  Badges sit outside their box (left, else above, else right), clear of other boxes; small boxes get
  a leader line.

crop: one zoomed close-up per callout, for findings too small to read on the full shot (< ~60px tall).
  python3 annotate.py crop IN.png CALLOUTS.json OUT_PREFIX [--zoom 3] [--pad 24] [--only 6 7] [--scale 2]
  writes OUT_PREFIX-<n>.png: the box plus padding, enlarged, outlined, numbered, note on top.

--no-caption (mark and crop): burn in no text at all. mark draws the boxes only (no number badges, no
  legend); crop writes the outlined close-up only (no number, no note header). Use it for images another
  judge reads, so the auditor's note cannot leak the verdict.

strip: put scroll frames (shoot.py --frames) side by side with a label under each, for motion review.
  python3 annotate.py strip F0.png F1.png F2.png OUT.png [--labels "0" "600" "1200"] [--title "..."] [--height 640]

pair: put a Before and an After image side by side (or stacked when wide).
  python3 annotate.py pair BEFORE.png AFTER.png OUT.png [--title "Checkout, 390px"]

Typical flow: mark before.png with the finding numbers, mark after.png with the
same numbers on the fixed regions, then pair the two marked images.
"""
import argparse
import json
import sys

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size):
    for f in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def wrap(draw, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _hits(c, rect, pad=0):
    """circle (cx, cy, r) intersects rect [x0, y0, x1, y1] grown by pad"""
    cx, cy, r = c
    x0, y0, x1, y1 = rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad
    nx, ny = min(max(cx, x0), x1), min(max(cy, y0), y1)
    return (cx - nx) ** 2 + (cy - ny) ** 2 < r * r


def place_badges(boxes, size, r, gap):
    """Badge outside its box: left, else above, else right, else below; off every other box and
    badge. Small boxes (under ~40px) get the badge set further out plus a leader line."""
    W, H = size
    rects = [[x, y, x + w, y + h] for x, y, w, h in boxes]
    placed, out = [], []
    for i, (x, y, w, h) in enumerate(boxes):
        far = min(w, h) < 40 * (r / 15)
        d = gap + (r if far else 0)
        cy_side = y + min(h / 2, r + 2)
        cx_top = x + min(w / 2, r + 2)
        cands = [(x - d - r, cy_side), (cx_top, y - d - r), (x + w + d + r, cy_side), (cx_top, y + h + d + r),
                 (x - d - r, y - d - r), (x + w + d + r, y - d - r)]
        if not far:  # next ring out, with a leader line
            cands += [(x - d - 3 * r, cy_side), (cx_top, y - d - 3 * r), (x + w + d + 3 * r, cy_side)]

        def ok(c, own=True):
            cx, cy = c
            if cx - r < 1 or cy - r < 1 or cx + r > W - 1 or cy + r > H - 1:
                return False
            if any(_hits((cx, cy, r), rc, 2) for j, rc in enumerate(rects) if j != i or own):
                return False
            return not any((cx - px) ** 2 + (cy - py) ** 2 < (2 * r + 3) ** 2 for px, py in placed)
        pick = next((c for c in cands if ok(c)), None)
        if pick is None:  # allow covering other boxes, never its own
            pick = next((c for c in cands if ok(c, own=False)), None)
        if pick is None:  # crowded edge: clamp inside the image at the box's left edge
            pick = (min(max(x - d - r, r + 2), W - r - 2), min(max(cy_side, r + 2), H - r - 2))
        placed.append(pick)
        rect = rects[i]
        nx, ny = min(max(pick[0], rect[0]), rect[2]), min(max(pick[1], rect[1]), rect[3])
        lead = (nx, ny) if (pick[0] - nx) ** 2 + (pick[1] - ny) ** 2 > (r + gap + 2) ** 2 else None
        out.append((pick, lead))
    return out


def check_bounds(callouts, size, src):
    """Exit non-zero when a box is mostly off the image: CSS-px boxes below the first viewport
    do not belong on the viewport shot. Warn on boxes that are only partly off."""
    W, H = size
    bad, part = [], []
    for c in callouts:
        x, y, w, h = c["box"]
        ix = max(0, min(x + w, W) - max(x, 0))
        iy = max(0, min(y + h, H) - max(y, 0))
        frac = (ix * iy) / max(1, w * h)
        if frac < 0.5:
            bad.append(c["n"])
        elif frac < 1:
            part.append(c["n"])
    if part:
        print(f"warning: callout(s) {part} are partly outside {src} ({W}x{H}); boxes are clipped", file=sys.stderr)
    if bad:
        sys.exit(f"error: callout(s) {bad} lie outside {src} ({W}x{H}). Boxes are page CSS px: mark findings "
                 f"below the first viewport on <name>-W-full.png, or pass --offset 0 <scrollY> for a scrolled shot.")


def load_callouts(a):
    callouts = json.load(open(a.callouts))
    ox, oy = a.offset
    return [dict(c, box=[(c["box"][0] - ox) * a.scale, (c["box"][1] - oy) * a.scale,
                         c["box"][2] * a.scale, c["box"][3] * a.scale]) for c in callouts]


def mark(a):
    im = Image.open(a.input).convert("RGB")
    callouts = load_callouts(a)
    check_bounds(callouts, im.size, a.input)
    color = hex_rgb(a.color)
    scale = max(1.0, im.width / 800, a.scale)
    stroke = int(3 * scale)
    r = int(15 * scale)
    fb = font(int(17 * scale))
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for c in callouts:
        x, y, w, h = c["box"]
        od.rounded_rectangle([x, y, x + w, y + h], radius=int(8 * scale), fill=color + (28,))
    im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(im)
    for c in callouts:
        x, y, w, h = c["box"]
        d.rounded_rectangle([x, y, x + w, y + h], radius=int(8 * scale), outline=color, width=stroke)
    if a.no_caption:
        im.save(a.output, optimize=True)
        print(a.output)
        return
    for c, ((cx, cy), lead) in zip(callouts, place_badges([c["box"] for c in callouts], im.size, r, int(4 * scale))):
        if lead:
            d.line([(cx, cy), lead], fill=color, width=max(2, stroke - 1))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline="white", width=max(2, stroke - 1))
        d.text((cx, cy), str(c["n"]), fill="white", font=fb, anchor="mm")
    if a.legend:
        fl = font(int(15 * scale))
        pad = int(16 * scale)
        lines = []
        for c in callouts:
            for i, ln in enumerate(wrap(d, f"{c['n']}. {c.get('note', '')}", fl, im.width - 2 * pad)):
                lines.append(ln if i == 0 else "    " + ln)
        lh = int(22 * scale)
        legend = Image.new("RGB", (im.width, pad * 2 + lh * len(lines)), "white")
        ld = ImageDraw.Draw(legend)
        for i, ln in enumerate(lines):
            ld.text((pad, pad + i * lh), ln, fill=(24, 24, 27), font=fl)
        out = Image.new("RGB", (im.width, im.height + legend.height), "white")
        out.paste(im, (0, 0))
        out.paste(legend, (0, im.height))
        im = out
    im.save(a.output, optimize=True)
    print(a.output)


def crop(a):
    """One zoomed close-up per callout: box plus padding, enlarged, outlined, with its number and note."""
    im = Image.open(a.input).convert("RGB")
    callouts = load_callouts(a)
    check_bounds(callouts, im.size, a.input)
    color = hex_rgb(a.color)
    want = set(a.only or [])
    for c in callouts:
        if want and c["n"] not in want:
            continue
        x, y, w, h = c["box"]
        pad = a.pad * a.scale
        x0, y0 = max(0, int(x - pad)), max(0, int(y - pad))
        x1, y1 = min(im.width, int(x + w + pad)), min(im.height, int(y + h + pad))
        z = min(a.zoom / a.scale, 1200 / max(1, x1 - x0))  # final zoom is relative to CSS px
        z = max(z, 1.0)
        cut = im.crop((x0, y0, x1, y1)).resize((round((x1 - x0) * z), round((y1 - y0) * z)), Image.LANCZOS)
        d = ImageDraw.Draw(cut)
        bx = [(x - x0) * z, (y - y0) * z, (x + w - x0) * z, (y + h - y0) * z]
        d.rounded_rectangle(bx, radius=8, outline=color, width=4)
        path = f"{a.prefix}-{c['n']}.png"
        if a.no_caption:
            cut.save(path, optimize=True)
            print(path)
            continue
        fl = font(22)
        ow = max(cut.width, 480)  # room for the note on narrow crops
        lines = wrap(d, f"{c['n']}. {c.get('note', '')}", fl, ow - 70)
        head = 18 + 30 * len(lines)
        out = Image.new("RGB", (ow, cut.height + head), (244, 244, 245))
        ImageDraw.Draw(out).rectangle([0, 0, ow, head], fill="white")
        out.paste(cut, ((ow - cut.width) // 2, head))
        od = ImageDraw.Draw(out)
        rr = 15
        od.ellipse([12, 9, 12 + 2 * rr, 9 + 2 * rr], fill=color)
        od.text((12 + rr, 9 + rr), str(c["n"]), fill="white", font=font(17), anchor="mm")
        for i, ln in enumerate(lines):
            od.text((52, 12 + 30 * i), ln.split(". ", 1)[1] if i == 0 and ". " in ln else ln, fill=(24, 24, 27), font=fl)
        out.save(path, optimize=True)
        print(path)


def pair(a):
    b = Image.open(a.before).convert("RGB")
    f = Image.open(a.after).convert("RGB")
    wide = b.width > 900
    gap, label_h = 24, 44
    title_h = 52 if a.title else 0
    if wide:  # stack desktop shots vertically
        w = max(b.width, f.width)
        H = title_h + 2 * label_h + b.height + f.height + gap
        sheet = Image.new("RGB", (w + 2 * gap, H + gap), (244, 244, 245))
        pos = [(gap, title_h + label_h), (gap, title_h + 2 * label_h + b.height + gap)]
    else:  # mobile shots side by side, bottom-aligned to the taller one
        h = max(b.height, f.height)
        W = b.width + f.width + 3 * gap
        sheet = Image.new("RGB", (W, title_h + label_h + h + gap), (244, 244, 245))
        pos = [(gap, title_h + label_h), (2 * gap + b.width, title_h + label_h)]
    d = ImageDraw.Draw(sheet)
    if a.title:
        d.text((gap, 14), a.title, fill=(24, 24, 27), font=font(24))
    for (x, y), img, lab, col in ((pos[0], b, "Before", (161, 161, 170)), (pos[1], f, "After", (22, 163, 74))):
        d.text((x, y - 34), lab, fill=col, font=font(22))
        sheet.paste(img, (x, y))
        d.rectangle([x - 1, y - 1, x + img.width, y + img.height], outline=(212, 212, 216))
    sheet.save(a.output, optimize=True)
    print(a.output)


def strip(a):
    frames = [Image.open(f).convert("RGB") for f in a.frames]
    frames = [f.resize((round(f.width * a.height / f.height), a.height)) if f.height != a.height else f for f in frames]
    gap, label_h = 16, 36
    title_h = 52 if a.title else 0
    W = sum(f.width for f in frames) + gap * (len(frames) + 1)
    sheet = Image.new("RGB", (W, title_h + a.height + label_h + gap), (244, 244, 245))
    d = ImageDraw.Draw(sheet)
    if a.title:
        d.text((gap, 14), a.title, fill=(24, 24, 27), font=font(24))
    x = gap
    labels = a.labels or [str(i) for i in range(len(frames))]
    for f, lab in zip(frames, labels + [""] * len(frames)):
        sheet.paste(f, (x, title_h))
        d.rectangle([x - 1, title_h - 1, x + f.width, title_h + f.height], outline=(212, 212, 216))
        d.text((x + f.width // 2, title_h + f.height + 18), lab, fill=(82, 82, 91), font=font(16), anchor="mm")
        x += f.width + gap
    sheet.save(a.output, optimize=True)
    print(a.output)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mark")
    m.add_argument("input")
    m.add_argument("callouts")
    m.add_argument("output")
    m.add_argument("--legend", action="store_true")
    m.add_argument("--no-caption", action="store_true", help="boxes only: no number badges, no legend")
    m.add_argument("--color", default="#E5484D")
    m.add_argument("--scale", type=float, default=1.0, help="image px per CSS px (devicePixelRatio)")
    m.add_argument("--offset", type=float, nargs=2, default=[0, 0], metavar=("X", "Y"),
                   help="page CSS px at the image's top-left (scrolled viewport shot)")
    cr = sub.add_parser("crop")
    cr.add_argument("input")
    cr.add_argument("callouts")
    cr.add_argument("prefix")
    cr.add_argument("--zoom", type=float, default=3.0, help="zoom relative to CSS px (capped at 1200px wide)")
    cr.add_argument("--pad", type=float, default=24, help="CSS px of context around the box")
    cr.add_argument("--only", type=int, nargs="*", help="callout numbers to crop (default all)")
    cr.add_argument("--color", default="#E5484D")
    cr.add_argument("--no-caption", action="store_true", help="outlined close-up only: no number, no note")
    cr.add_argument("--scale", type=float, default=1.0)
    cr.add_argument("--offset", type=float, nargs=2, default=[0, 0], metavar=("X", "Y"))
    p = sub.add_parser("pair")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("output")
    p.add_argument("--title", default="")
    st = sub.add_parser("strip")
    st.add_argument("paths", nargs="+", help="frame images, then the output path")
    st.add_argument("--labels", nargs="*", default=[])
    st.add_argument("--title", default="")
    st.add_argument("--height", type=int, default=640)
    a = ap.parse_args()
    if a.cmd == "mark" and a.no_caption and a.legend:
        sys.exit("--no-caption and --legend conflict: the legend is burned-in text")
    if a.cmd == "strip":
        if len(a.paths) < 2:
            sys.exit("strip needs at least one frame and an output path")
        a.frames, a.output = a.paths[:-1], a.paths[-1]
    {"mark": mark, "crop": crop, "pair": pair, "strip": strip}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

"""Turn one set of green-screen frames into the transparent WebPs the page loads.

    python3 tools/loading-frames.py <src-dir> <out-dir> [options]

      --preview=<dir>     write a contact sheet there
      --extend=a2,a3      on these frames, carry the left-hand dissolve up as far
                          as there is anything in it to dissolve, instead of
                          stopping at the desk

One set per run. A set is a directory of PNGs whose names sort into the order
they play, with the completion frames marked by "Done" — which is how the
generator names them, and the only convention this relies on. They come out as
1..N.webp and a1..aN.webp, which is what loadingFrames.tsx counts.

The order of the steps matters more than any single one. The screen is taken back
out of the picture at full resolution, before anything is resized or re-encoded.
Run it the other way round and the arithmetic is being applied to pixels that
have already been averaged with their neighbours and thrown through a lossy
encoder, which is how the first attempt turned a halo magenta.

Three things are measured rather than declared, because there is more than one
set now and the second batch matched the first in none of them:

  the screen      taken from a ring of border pixels, per frame. The three sets
                  came out of three renders and no two screens are the same
                  colour. A median and not a mean: the drawing reaches the left
                  and right edges, and a mean quietly averages it in.
  the crop        placed from the character and the desk line, at a size that is
                  the same for every set. One box per set — a box fitted per frame
                  is a character that jitters — and the same shape for all of
                  them, which is what makes the three characters the same size.

                  The first attempt fitted the box to each set's own ink and
                  bottomed it on the desk's underside, and the characters came out
                  looking different sizes. They are not: measured, the three are
                  480, 482 and 493 source pixels tall, within three per cent of
                  each other. What differed was the desk — 224 pixels of it on one
                  set and 95 on another — so a box of fixed height bottomed under
                  the desk left 225 pixels of air over one character and 341 over
                  another, and the one with more air above it reads as smaller.
                  So the desk is trimmed to the same depth on all of them, and
                  what is left over is the same everywhere.
  the desk line   the band at the bottom that runs nearly the full width, which
                  is the desk and nothing else: the character never comes close to
                  both edges. Both of its edges are used — the dissolve starts at
                  the top of the band, and the crop stops at the bottom of it.

                  Nearly, and not exactly: a desk drawn in perspective has a top
                  face narrower than its front, inset at both ends. Requiring the
                  full width found the front face only, and left the top face
                  sitting above the dissolve as an opaque corner with nothing
                  under it. Nine tenths finds both. It is not looser than it can
                  afford to be — on one set the character's own widest rows reach
                  four fifths, and eight tenths would have taken the dissolve up
                  into its wings.
  the frames      counted from the directory.

What is still declared is the output size, the alpha floor, and where the
generator puts its watermark. That last one is a sparkle at the bottom right of
every frame, and the only reason it is never seen is that the crop stops above
it — so the crop is not allowed past it.
"""

from PIL import Image
from scipy import ndimage
import numpy as np
import os
import sys
import glob

SRC = sys.argv[1]
OUT_DIR = sys.argv[2]
FLAGS = dict(a.split("=", 1) for a in sys.argv[3:] if a.startswith("--"))
PREVIEW = FLAGS.get("--preview")
EXTEND = set(filter(None, FLAGS.get("--extend", "").split(",")))

OUT = (384, 496)          # twice the 192 x 248 the page draws
FADE = 90                 # how far the desk's left-hand cut dissolves over

# The box, in source pixels, and the same for every set.
#
# The width is what the widest character needs plus a margin — 659 and 20 either
# side — and the height follows from the output's shape. Nothing much smaller is
# available: the character is landscape, about 660 by 490, and the frame is
# portrait, so fitting the wingspan is what decides the scale and the character
# fills a little over half the height and no more. Trying for more is a cut
# wingtip.
BOX_W = 700
BOX_H = round(BOX_W / (OUT[0] / OUT[1]))
# How much desk is left under the character. A constant, which is the whole point
# — see the note on the crop above — and enough for a top board and not the rail
# under it.
DESK_SHOWN = 84
FLOOR = 0.12              # below this a pixel is screen, not drawing
TRUST = 0.55              # above this a pixel's colour can be worked back out
WATERMARK_TOP = 1130      # the generator's sparkle sits at y 1139-1181


def keyed(path: str):
    """One frame, as straight colour and alpha at full resolution."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)

    ring = np.concatenate([a[:6].reshape(-1, 3), a[-6:].reshape(-1, 3),
                           a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    key = np.median(ring, 0)

    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    score = g - np.maximum(r, b)

    # How much of the screen is showing through, measured against how green the
    # screen itself is rather than against the key colour. The screen is not
    # quite uniform, and a denominator a few counts too large leaves the whole
    # background at a few per cent of opacity instead of none — a haze over
    # everything, and a file three times the size for carrying it.
    clear = np.percentile(score, 99)
    alpha = np.clip(1 - score / clear, 0, 1)
    # And the last of it clipped away, so that background is background.
    alpha = np.clip((alpha - FLOOR) / (1 - FLOOR), 0, 1)

    # That formula reads the drawing's own greens as screen showing through, and
    # a mint wingtip is green enough to be made translucent by it. But the
    # drawing is sealed inside dark outlines: fill whatever those outlines
    # enclose and the inside is locked solid, while a soft edge outside them
    # keeps its gradient.
    #
    # Not everything enclosed is drawing, though, and that is what the second
    # test is for. The gap between a raised wing and the body is sealed by
    # outlines too, and filling it locked a slab of screen in as opaque green —
    # eight and a half thousand pixels of it on the spyglass set. So an enclosed
    # pixel only counts as drawing if it is meaningfully less green than the
    # screen. The margin is wide: a mint wingtip scores about 20 against a screen
    # of 111, and the slabs behind the wings scored 104.
    inside = score < 10
    enclosed = ndimage.binary_fill_holes(inside) & (score < clear * 0.6)
    alpha = np.maximum(alpha, enclosed)

    # An edge is the drawing blended with the screen behind it:
    #     seen = drawing * alpha + screen * (1 - alpha)
    # so the drawing on its own is that run backwards. This is the step that
    # matters: it does not remove the green from an edge, it works out what
    # colour the edge was before the green was ever behind it.
    safe = np.maximum(alpha, FLOOR)[..., None]
    colour = np.clip((a - key * (1 - safe)) / safe, 0, 255)

    # A ceiling on what green is left where a pixel is solid. Set above the
    # drawing's own greenest ratio, so a wingtip is never touched.
    colour[..., 1] = np.minimum(
        colour[..., 1], np.maximum(colour[..., 0], colour[..., 2]) * 1.25)

    # Under a soft edge, that division is by as little as the floor — an eight
    # times amplification of whatever noise the render left — and what came back
    # was a magenta thread following the whole silhouette. Below TRUST there is
    # not enough of the drawing left in the pixel to work its colour out of, so
    # take the nearest colour that there is. The alpha is untouched: the edge
    # keeps its softness and only stops inventing a colour for it.
    trust = alpha >= TRUST
    nearest = ndimage.distance_transform_edt(
        ~trust, return_distances=False, return_indices=True)
    colour = np.where(trust[..., None], colour, colour[nearest[0], nearest[1]])

    return colour, alpha


def desk_band(frames) -> tuple:
    """The desk's top and bottom rows, in source coordinates.

    Found rather than declared: the sets put the character at different heights
    and their desks are different thicknesses. What identifies the desk is that
    it is the only thing that comes close to spanning the frame.
    """
    mask = np.zeros(frames[0][1].shape, bool)
    for _, alpha in frames:
        mask |= alpha > 0.5
    mask[WATERMARK_TOP:] = False
    covered = mask.mean(1)
    y = len(covered) - 1
    while y >= 0 and covered[y] < 0.90:      # past the empty rows under the desk
        y -= 1
    bottom = y
    while y >= 0 and covered[y] >= 0.90:     # and up through the desk itself
        y -= 1
    return (y + 1, bottom) if bottom >= 0 else (None, None)


def crop_for(frames, band) -> tuple:
    """The box: fixed in size, placed on this set's character and desk.

    Bottomed a constant distance below the desk's top edge rather than on its
    underside. The underside is where the legs are, and on one set they are
    stubs — a couple of dozen pixels below an otherwise solid bar, hanging in the
    air because the drawing has them running off the bottom of a frame taller than
    this one. It is also where the rail under a table's top board is, which is a
    second piece of furniture nobody asked to see.

    Centred on the character and not on the frame, because the three sets do not
    put it in the same place. What comes back is not clamped to the source: above
    the character there is nothing but screen, so padding with transparency where
    the box hangs over the top is the same picture.
    """
    mask = np.zeros(frames[0][1].shape, bool)
    for _, alpha in frames:
        mask |= alpha > 0.5
    mask[WATERMARK_TOP:] = False
    above = mask[:band[0]] if band[0] is not None else mask
    cols = np.where(above.any(0))[0]
    middle = (int(cols.min()) + int(cols.max())) // 2 if len(cols) \
        else mask.shape[1] // 2
    rows = np.where(mask.any(1))[0]
    line = band[0] if band[0] is not None else int(rows.max())
    bottom = min(line + DESK_SHOWN, WATERMARK_TOP)
    left = middle - BOX_W // 2
    return (left, bottom - BOX_H, left + BOX_W, bottom)


def fade_from(alpha, box, desk, extend: bool) -> int:
    """Which row the left-hand dissolve starts on, in cropped coordinates.

    The desk, normally. `extend` is for a frame where something else is cut by
    that edge and should go with it — the night set lowers its spyglass across the
    cut on the last two of its completion frames, and a desk that dissolves out
    from under a spyglass that does not is worse than neither dissolving. Where it
    is asked for, the start is still measured and not chosen: it is the topmost
    row with anything in the dissolve's own width, which is the same question the
    desk answers on every other frame.
    """
    if not extend:
        return desk
    band = cut(alpha, box)[:, :FADE] > 0.4
    rows = np.where(band.any(1))[0]
    return int(rows.min()) if len(rows) else desk


def cut(plane, box):
    """The box out of a full frame, padded where the box hangs over an edge."""
    h, w = plane.shape[:2]
    shape = (box[3] - box[1], box[2] - box[0]) + plane.shape[2:]
    out = np.zeros(shape, plane.dtype)
    sy0, sy1 = max(0, box[1]), min(h, box[3])
    sx0, sx1 = max(0, box[0]), min(w, box[2])
    out[sy0 - box[1]:sy1 - box[1], sx0 - box[0]:sx1 - box[0]] = \
        plane[sy0:sy1, sx0:sx1]
    return out


def framed(colour, alpha, box, fade_top) -> Image.Image:
    colour = cut(colour, box)
    alpha = cut(alpha, box)

    # The desk runs off both sides of the frame, so the crop leaves it ending in
    # mid-air. The right-hand cut lands on the edge of the window and is never
    # seen; the left one dissolves. Only below the desk's own top edge, which is
    # why it never reaches a wing.
    ramp = np.clip(np.arange(alpha.shape[1]) / FADE, 0, 1)[None, :]
    band = np.zeros((alpha.shape[0], 1))
    band[fade_top:] = 1
    alpha = alpha * (1 - band * (1 - ramp))
    # Resized with the colour already multiplied by the alpha, and divided back
    # out afterwards. Resampling straight alpha mixes the colour of a nearly
    # transparent pixel into its opaque neighbours at full strength, which is
    # the other half of where that magenta thread came from; multiplied through,
    # a transparent pixel has no colour to contribute.
    pm = np.dstack([colour * alpha[..., None], alpha * 255])
    small = np.asarray(
        Image.fromarray(pm.astype(np.uint8), "RGBA").resize(OUT, Image.LANCZOS)
    ).astype(np.float32)
    a = small[..., 3:] / 255
    straight = np.clip(small[..., :3] / np.maximum(a, 1 / 255), 0, 255)
    return Image.fromarray(
        np.dstack([straight, small[..., 3]]).astype(np.uint8), "RGBA")


files = sorted(glob.glob(os.path.join(SRC, "*.png")))
loop = [f for f in files if "Done" not in os.path.basename(f)]
done = [f for f in files if "Done" in os.path.basename(f)]
names = [str(i + 1) for i in range(len(loop))] + \
        [f"a{i + 1}" for i in range(len(done))]

frames = [keyed(f) for f in loop + done]
band = desk_band(frames)
box = crop_for(frames, band)
desk = (band[0] - box[1]) if band[0] is not None else (box[3] - box[1])
os.makedirs(OUT_DIR, exist_ok=True)
print(f"{len(loop)} loop + {len(done)} completion   crop {box}"
      f"   desk rows {band[0]}-{band[1]}   dissolve from {desk}"
      + (f"   extended on {sorted(EXTEND)}" if EXTEND else ""))

total = 0
for (colour, alpha), name in zip(frames, names):
    top = fade_from(alpha, box, desk, name in EXTEND)
    im = framed(colour, alpha, box, top)
    path = os.path.join(OUT_DIR, f"{name}.webp")
    im.save(path, quality=80, method=6)
    total += os.path.getsize(path)
    # What is left of the screen, and what the un-blending overshot into. Both
    # should be a few hundred pixels of the drawing's own colour, not a thread
    # around its edge — check where they are, not just how many.
    a = np.asarray(im).astype(np.float32)
    green = (a[..., 3] > 5) & (a[..., 1] - np.maximum(a[..., 0], a[..., 2]) > 15)
    magenta = (a[..., 3] > 5) & (a[..., 1] < np.minimum(a[..., 0], a[..., 2]) - 8)
    print(f"  {name:3s} {os.path.getsize(path) / 1024:5.1f} KB"
          f"   green {int(green.sum()):5d}   magenta {int(magenta.sum()):5d}"
          f"{'   dissolve from ' + str(top) if top != desk else ''}")

print(f"  total {total / 1024:.0f} KB")

if PREVIEW:
    os.makedirs(PREVIEW, exist_ok=True)
    tw, th = OUT[0] // 2, OUT[1] // 2
    sheet = Image.new("RGB", (tw * len(names), th), (248, 250, 252))
    for i, name in enumerate(names):
        f = Image.open(os.path.join(OUT_DIR, f"{name}.webp")).resize((tw, th))
        sheet.paste(f, (i * tw, 0), f)
    sheet.save(os.path.join(PREVIEW, f"{os.path.basename(SRC)}_sheet.png"))

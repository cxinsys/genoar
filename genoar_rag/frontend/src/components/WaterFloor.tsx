"use client";

import { useEffect, useRef } from "react";
import { usePaletteId } from "@/hooks/usePaletteId";

/** The floor of a shallow bay, computed rather than drawn.
 *
 * It must not be a picture being moved, however cleverly. That is the one
 * thing caustics cannot be: a rigid transform carries a shape around without
 * changing it, and what water does is change it — veins bend, pinch, break and
 * join up somewhere else. Sliding, turning, stacking and erasing can get close
 * to that and never be it, because none of them can alter the shape of the
 * thing they act on.
 *
 * So this works out the field for every pixel of every frame, which is the only
 * way to have one that genuinely deforms, and does it on the GPU, where that is
 * a few million cheap operations and costs nothing measurable — less than
 * compositing two layers of picture would cost.
 *
 * The physics is honest, at least in outline. Light through a wavy surface is
 * focused onto the places where the surface happens to curve the right way, and
 * those places are a level set of the wave. So: build a wave as the sum of two
 * noise fields sliding across each other on different courses — a sum that
 * genuinely writhes, because neither term is where it was a moment ago relative
 * to the other — and then take its contours. The shadow is the same contour
 * read a little way off, which is what a shadow is.
 */

/** A full-screen triangle. Cheaper than two triangles for a quad and it has no
 *  seam down the diagonal, which matters not at all here but costs nothing. */
const VERTEX = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

const FRAGMENT = `
/* highp where there is any, and it is not a nicety. The hash below multiplies a
   sine by forty-three thousand and keeps the fraction, which needs most of a
   float's mantissa to mean anything; at mediump it degenerates into bands or
   into a constant, and a constant field is a flat colour. */
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif

uniform vec2 uSize;
uniform float uTime;
uniform vec3 uLight;
uniform vec3 uShade;
uniform float uLightA;
uniform float uShadeA;
uniform float uScale;
uniform float uSpeed;
uniform vec2 uDrift;
uniform float uWarp;
uniform float uRoll;
uniform float uCore;
uniform float uGlow;
uniform vec2 uCast;
uniform float uCalmHold;
uniform float uCalmFade;
uniform vec3 uLift;
uniform float uLiftA;
uniform float uLiftTo;

/* No sine in it, and that is the point. The usual one-liner takes a sine and
   keeps the fraction of it multiplied by a large number — a transcendental, on a
   unit that has few of them, asked for sixty times a pixel here. This is a
   handful of multiplies and fracts, several times cheaper, and no worse:
   nothing downstream can tell where its randomness came from once it has been
   summed over three octaves and contoured. What it buys is the resolution. */
float hash(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

/* Value noise, smoothstepped between lattice points. Not the prettiest noise
   there is, but it is four hashes and the difference does not survive being
   summed four times and then contoured. */
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int k = 0; k < 3; k++) {
    v += a * noise(p);
    p = p * 2.07 + 3.1;
    a *= 0.5;
  }
  return v;
}

/* Two numbers from one cell, for where its point sits inside it. */
vec2 hash2(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * vec3(0.1031, 0.1030, 0.0973));
  q += dot(q, q.yzx + 33.33);
  return fract((q.xx + q.yz) * q.zy);
}

/* A triangle wave, smoothed at the turns.
   The points have to wander and they have to still be where they were a moment
   ago — a wrap would teleport one across its cell and take a whole junction of
   the net with it. Out and back has no such moment, and the smoothing takes the
   corner off the turn so it does not read as a bounce. No trigonometry: this is
   evaluated eighteen times a pixel and a sine there would cost more than
   everything else here put together. */
vec2 turn(vec2 x) {
  vec2 f = abs(fract(x) * 2.0 - 1.0);
  return f * f * (3.0 - 2.0 * f);
}

/* Where a cell's point is now. Each drifts at its own rate, so neighbours pull
   apart and close up and the borders between them swing, lengthen and vanish —
   cells genuinely change who they are next to. That is the deformation, and it
   is the shape of the thing rather than a trick played on top of it. */
vec2 site(vec2 cell, float t) {
  vec2 o = hash2(cell);
  return 0.15 + 0.7 * turn(o + t * (0.5 + o));
}

/* A minimum with the corner taken off it.
   Where two borders meet, a plain min leaves the sharp notch that makes a cell
   diagram look like a diagram. This blends the two over a short distance
   instead, so junctions arrive rounded. The width of the blend is tied to the
   thickness of a vein rather than given a control of its own — a junction
   should be about as round as the lines meeting in it are wide, and no setting
   of the two independently looked better than that. */
float softmin(float a, float b, float k) {
  float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
  return mix(b, a, h) - k * h * (1.0 - h);
}

/* Distance to the nearest border between cells.
 *
 * Two passes, which is what it takes: the first finds which cell this pixel is
 * in, and the second measures to the bisector between that cell's point and
 * each of its neighbours'. Measuring to the points instead would give the
 * distance to a centre, and the set of places equally far from two centres is
 * the border — you cannot get it from one pass.
 *
 * This is the whole reason for the change. Contours of a smooth field are
 * closed loops nested inside one another, which is a survey map and reads as
 * one however it is coloured. Borders between cells meet three at a time, at
 * junctions, and enclose rooms of unequal size — which is what a caustic net
 * is, and what the sea in a cel-shaded game is drawn as. */
float borders(vec2 p) {
  float t = uTime * uSpeed;

  /* The plane is bent before the cells are cut out of it.
     A border between two cells is a straight segment — that is what being
     equidistant from two points means, and no amount of rounding the ends will
     make a straight line curve. So the plane itself is warped first: the cells
     are still built on straight lines, but the lines lie in a space that is no
     longer flat, and what comes out are curves.

     The bend travels too, and how fast is the one number here that has to be
     kept small. Everything else in this shader either sits still or moves the
     water forwards; this one moves the water sideways and can move it
     backwards. The bend is worth up to --water-warp of displacement, so its
     rate of change is that many cells per second of apparent motion, and when
     it happens to run against the drift the two subtract. Tie the coefficient
     to --water-speed, which has no business setting it, and the bend is worth
     about half the drift: the sea visibly slows, lags and picks up again every
     half minute or so. It reads as a loop, and there is no loop here to blame.

     Kept an order of magnitude under the drift, it reads as what it is: the
     water bending as it goes rather than sloshing where it stands. */
  vec2 q = p + uWarp * vec2(
    fbm(p * 0.5 + vec2(0.0, uRoll * uTime)) - 0.44,
    fbm(p * 0.5 + vec2(4.7, 2.1) - vec2(0.73 * uRoll * uTime, 0.0)) - 0.44);

  vec2 n = floor(q);
  vec2 f = fract(q);

  vec2 mr = vec2(0.0);
  vec2 mg = vec2(0.0);
  float md = 8.0;
  for (int j = -1; j <= 1; j++) {
    for (int i = -1; i <= 1; i++) {
      vec2 g = vec2(float(i), float(j));
      vec2 r = g + site(n + g, t) - f;
      float d = dot(r, r);
      if (d < md) {
        md = d;
        mr = r;
        mg = g;
      }
    }
  }

  md = 8.0;
  for (int j = -1; j <= 1; j++) {
    for (int i = -1; i <= 1; i++) {
      vec2 g = mg + vec2(float(i), float(j));
      vec2 r = g + site(n + g, t) - f;
      vec2 away = r - mr;
      if (dot(away, away) > 0.00001) {
        md = softmin(md, dot(0.5 * (mr + r), normalize(away)), uCore * 0.9);
      }
    }
  }
  return md;
}

/* A vein, and the light around it.
 *
 * Two curves and not one. A falloff alone reaches full brightness at a single
 * point and nowhere else — the bright part is a hairline whatever number it is
 * given, and softening it only widens the dim skirt. Thin and blurry at once,
 * which is no use to anyone.
 *
 * So: a core and a halo. The core is a smoothstep with its top clipped off, so
 * there is a band of real width that is properly bright; the halo falls away
 * past it. Taking the larger lays one inside the other. Both are measured in
 * cells now rather than as a fraction of a contour spacing, which is why they
 * hold their look when the water is made coarser or finer. */
float veins(vec2 p) {
  float b = borders(p);
  float core = smoothstep(uCore, uCore * 0.35, b);
  float halo = exp(-b * uGlow);
  return max(core, halo);
}

void main() {
  /* How far along the diagonal from the top left corner this pixel lies, nought
     at that corner and one at the opposite one. gl_FragCoord counts y up from
     the bottom, so it is flipped to read the way a page does.

     Water everywhere at once is water nobody can read past, so it is held back
     where the page starts — the corner the eye lands on and where a heading and
     the first card sit — and let out towards the far one, which is margin.

     Done here rather than as a pane laid over the top, and there is a reason
     beyond one element fewer. A pane in the page's own colour and a fade of
     these caustics to nothing are the same picture, because the only thing
     behind them is that colour. But a pane costs a second full-window surface
     to composite every frame, and this costs a subtraction — and it lets the
     next four lines exist. */
  vec2 g = gl_FragCoord.xy / uSize;
  float corner = (g.x + (1.0 - g.y)) * 0.5;
  float show = smoothstep(uCalmHold, uCalmFade, corner);

  /* And the corner itself lifted, which is the other half of the same gradient.
   *
   * Three states across the diagonal, and only two of them are painted. It
   * begins as a colour lighter than the page, opaque; that colour thins until
   * what is left is the page itself; and then the water comes up out of it.
   * The middle state needs no paint at all — being the page's own colour, it is
   * exactly what showing nothing looks like — so the whole run is one wash
   * losing its alpha followed by the caustics gaining theirs. */
  float lift = (1.0 - smoothstep(0.0, uLiftTo, corner)) * uLiftA;
  vec4 wash = vec4(uLift * lift, lift);

  /* Nothing to work out, which is the point: the field below is the whole cost
     of this shader, and roughly a quarter of the window is under the calm at
     any moment. The wash still goes out — it is two multiplies — but a quarter
     of the real work is not spent. */
  if (show <= 0.004) {
    gl_FragColor = wash;
    return;
  }

  /* Divided by width on both axes, so a vein is as wide as it is tall whatever
     shape the window is. */
  vec2 p = gl_FragCoord.xy / uSize.x * uScale;

  /* The whole bay running down and to the right, and no loop in sight.
     Not a picture sliding by exactly one of its own tiles: that is seamless on
     paper only, because the slack it needs makes a layer wider than a GPU will
     hold in one texture, and the seam then shows every time round. There is no
     tile here. The field is worked out from the coordinate, so it exists
     everywhere and repeats nowhere, and moving through it is subtracting from
     that coordinate. It never returns to where it began, so it never has to be
     got back there.
     Subtracted rather than added, and the second one negated: moving where you
     look moves what you see the other way, and this shader counts y upwards
     while the sea is meant to run down the screen. */
  p -= vec2(uDrift.x, -uDrift.y) * uTime;

  /* Worked out once for both, and not inside each: that would be a third of
     the shader's cost spent twice on the same answer. Sharing it is also the
     truer answer — a vein and the shadow it throws are one thing seen twice, so
     when one fades the other has to fade with it. */
  float dim = 0.45 + 0.55 * fbm(p * 0.55 - vec2(0.07, 0.05) * uTime * uSpeed);

  float lit = veins(p) * dim;
  /* Not called cast, however much it wants to be: cast is a reserved word in
     GLSL, left over from a type conversion syntax the language never shipped. */
  float dark = veins(p - uCast) * dim;

  /* Premultiplied, and composited here rather than left to two canvases: the
     light goes over its own shadow, and the page shows through both. */
  vec4 li = vec4(uLight * (lit * uLightA), lit * uLightA);
  vec4 sh = vec4(uShade * (dark * uShadeA), dark * uShadeA);
  /* Premultiplied throughout, so the fade is one multiply and stays correct. */
  vec4 water = (li + sh * (1.0 - li.a)) * show;
  gl_FragColor = water + wash * (1.0 - water.a);
}
`;

/** How much smaller than the window the field is worked out, when the
 *  stylesheet does not say.
 *
 *  This is where sharpness actually comes from. Whatever the shader draws is
 *  stretched back up to the window by the browser, and a stretch is a blur — at
 *  a third of the width, a three-pixel one, and none of it deliberate.
 *
 *  It is also the expensive number, and quadratically: half again as wide is
 *  more than twice the pixels. That is what the hash and the shared dim buy —
 *  between them they pay for this. */
const DETAIL = 0.55;

function reading(style: CSSStyleDeclaration, name: string, fallback: number) {
  const value = parseFloat(style.getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

function colour(
  style: CSSStyleDeclaration,
  name: string,
  fallback: [number, number, number],
): [number, number, number] {
  const parts = style
    .getPropertyValue(name)
    .trim()
    .split(/[\s,]+/)
    .map(Number);
  return parts.length === 3 && parts.every(Number.isFinite)
    ? (parts as [number, number, number])
    : fallback;
}

function compile(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    // Said out loud rather than swallowed. A driver that will not take this
    // shader is not something the reader can act on, but it is the only way
    // anyone would ever find out why the ground went plain.
    console.warn(
      "water: shader would not compile",
      gl.getShaderInfoLog(shader),
    );
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

export default function WaterFloor() {
  /* One of the few things the stylesheet cannot answer. Everything else on this
     site is a custom property and no component knows which palette is showing;
     a canvas is not a paint, it is a program, and it either runs or it does
     not. Its colours still come from the stylesheet — see below — so the
     palette keeps the say over what it looks like, just not over whether it
     exists. */
  const palette = usePaletteId();
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    const gl = canvas.getContext("webgl", {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
    });
    // No WebGL, or the browser has refused a context: the palette simply has a
    // plain ground. Nothing here is information.
    // A context that is already gone. This is the ordinary case in development:
    // React runs an effect, tears it down and runs it again, and getContext
    // hands back the same context both times — so anything the cleanup did to
    // it, the second run inherits.
    if (!gl || gl.isContextLost()) return;

    const vs = compile(gl, gl.VERTEX_SHADER, VERTEX);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
    const program = vs && fs ? gl.createProgram() : null;
    if (!vs || !fs || !program) return;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn(
        "water: program would not link",
        gl.getProgramInfoLog(program),
      );
      return;
    }
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW,
    );
    const aPos = gl.getAttribLocation(program, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const at = (name: string) => gl.getUniformLocation(program, name);
    const uSize = at("uSize");
    const uTime = at("uTime");

    // The look, taken from the stylesheet once and again whenever the palette
    // changes, because the palette is what this effect is keyed on.
    const style = getComputedStyle(canvas);
    gl.uniform3fv(at("uLight"), colour(style, "--water-light", [1, 1, 1]));
    gl.uniform3fv(
      at("uShade"),
      colour(style, "--water-shade", [0.36, 0.56, 0.59]),
    );
    gl.uniform1f(at("uLightA"), reading(style, "--water-light-a", 0.5));
    gl.uniform1f(at("uShadeA"), reading(style, "--water-shade-a", 0.34));
    gl.uniform1f(at("uSpeed"), reading(style, "--water-speed", 0.16));
    gl.uniform2f(
      at("uDrift"),
      reading(style, "--water-drift-x", 0.018),
      reading(style, "--water-drift-y", 0.026),
    );
    gl.uniform1f(at("uWarp"), reading(style, "--water-warp", 0.55));
    gl.uniform1f(at("uRoll"), reading(style, "--water-warp-roll", 0.006));
    gl.uniform1f(at("uCore"), reading(style, "--water-core", 0.05));
    gl.uniform1f(at("uGlow"), reading(style, "--water-glow", 14));
    gl.uniform1f(at("uCalmHold"), reading(style, "--water-calm-hold", 0.16));
    gl.uniform1f(at("uCalmFade"), reading(style, "--water-calm-fade", 0.72));
    gl.uniform3fv(
      at("uLift"),
      colour(style, "--water-lift", [0.95, 0.99, 0.97]),
    );
    gl.uniform1f(at("uLiftA"), reading(style, "--water-lift-a", 1));
    gl.uniform1f(at("uLiftTo"), reading(style, "--water-lift-to", 0.42));
    // Negated, because the shader counts y upwards and a shadow falls down.
    gl.uniform2f(
      at("uCast"),
      reading(style, "--water-cast-x", 0.01),
      -reading(style, "--water-cast-y", 0.05),
    );

    /* The viewport and the size uniform are set every time, and only the
       drawing buffer is left alone when it already fits. They belong to
       different things: the buffer belongs to the canvas, which survives a
       teardown, and the uniform belongs to the program, which does not. Skip
       both together and on the second run of the effect — with the canvas
       already the right size — the new program never learns how big it is, and
       divides by nothing. */
    const detail = reading(style, "--water-detail", DETAIL);
    const uScale = at("uScale");
    const cells = reading(style, "--water-scale", 8);
    const cellsAt = reading(style, "--water-scale-at", 1920);
    const spread = reading(style, "--water-spread", 0.5);

    /* How many cells to fit across the window this wide.
     *
     * The shader divides by the window's width, so a fixed count means a cell
     * is always the same fraction of the window — and a fraction of a phone is
     * a very small thing. At eight across, a cell went from 240px on a desktop
     * to 49px on a handset: the same picture in name only, and far too fine to
     * read as water.
     *
     * The count therefore follows the width, but not all the way. Spread is
     * the exponent it follows it by: at nought the count is fixed and the cells
     * shrink with the window, at one the cells hold their size in pixels and
     * the count shrinks instead. Neither end is right — a phone should show
     * somewhat smaller cells, just not five times smaller — so the middle is
     * the default, and a square root turns that fivefold range into a bit over
     * two.
     *
     * Worked out here rather than in media queries because it belongs with the
     * resize: this runs whenever the box changes, so it follows a window being
     * dragged rather than stepping at whatever widths someone chose. */
    function scaleFor(width: number) {
      return cells * Math.pow(Math.max(width, 1) / cellsAt, spread);
    }

    function size() {
      if (!gl) return;
      const w = Math.max(1, Math.round(canvas!.clientWidth * detail));
      const h = Math.max(1, Math.round(canvas!.clientHeight * detail));
      if (canvas!.width !== w || canvas!.height !== h) {
        canvas!.width = w;
        canvas!.height = h;
      }
      gl.viewport(0, 0, w, h);
      gl.uniform2f(uSize, w, h);
      gl.uniform1f(uScale, scaleFor(canvas!.clientWidth));
    }
    size();

    const observer = new ResizeObserver(size);
    observer.observe(canvas);

    const still = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;

    /* Not every frame the display offers.
     *
     * Nothing here moves quickly — a vein takes a couple of minutes to cross
     * the window — and half the frames of something that slow are frames nobody
     * could tell were drawn. The animation still runs on the display's clock
     * and is still smooth in time; it is only redrawn on the way past. Half the
     * work for a difference that has to be looked for. */
    const step = 1000 / Math.max(1, reading(style, "--water-fps", 30));
    let last = -1e9;

    function draw(t: number) {
      if (!gl) return;
      gl.uniform1f(uTime, t / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    function run(t: number) {
      if (t - last >= step) {
        last = t;
        draw(t);
      }
      frame = requestAnimationFrame(run);
    }

    function start() {
      if (frame) return;
      // Still, and one frame of it: the field is worth having even when the
      // reader has asked for nothing to move.
      if (still.matches || document.hidden) {
        draw(0);
        return;
      }
      frame = requestAnimationFrame(run);
    }

    function stop() {
      if (!frame) return;
      cancelAnimationFrame(frame);
      frame = 0;
    }

    // A hidden tab still runs rAF in some browsers and none of them owe us a
    // frame; either way there is nobody looking at it.
    function onVisibility() {
      if (document.hidden) stop();
      else start();
    }
    document.addEventListener("visibilitychange", onVisibility);
    still.addEventListener("change", () => {
      stop();
      start();
    });

    // A lost context is not an error worth reporting to anyone — it happens when
    // the machine sleeps. Stop asking for frames and let the ground be plain.
    function onLost(e: Event) {
      e.preventDefault();
      stop();
    }
    canvas.addEventListener("webglcontextlost", onLost);

    start();

    return () => {
      stop();
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("webglcontextlost", onLost);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      // The context is deliberately left alone. It belongs to the canvas and
      // goes when the canvas does; taking it down here kills it for the next
      // run of this same effect, which in development is a certainty.
    };
  }, [palette]);

  if (palette !== "tropical") return null;
  return <canvas ref={ref} aria-hidden className="water-glass" />;
}

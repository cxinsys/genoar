/**
 * Groups a sample's tissue into the body system it belongs to.
 *
 * Colour is only worth showing where the values vary. Of everything a sample
 * carries, tissue is the only field that does: organism is 86% human, assay type
 * 82% RNA-Seq and platform 98% Illumina, so colouring by any of those paints
 * almost every card the same. Tissue's most common value covers 6% of the
 * corpus — but it has 2,383 spellings, far too many to colour one by one.
 *
 * Folding those spellings into ten systems gives both: no family holds more than
 * a quarter of the corpus, and a family means something a reader already knows.
 *
 * The matching is by keyword because the field is free text a submitter typed —
 * "Bone Marrow", "bone marrow aspirate", "BM (CD34+)" all have to land in the
 * same place. Roughly 85% of samples match; the rest are deliberately left
 * unfamilied rather than forced into the nearest bucket.
 */

export type TissueFamily = {
  key: string;
  label: string;
  /** The system's colour. Both the card band and the dashboard's composition
      bar read it from here, so the two cannot drift apart.

      Values rather than CSS custom properties: both readers are JavaScript, and
      a property declared in the stylesheet's theme block is inlined into the
      utilities that use it and then dropped, leaving a var() reached from JS
      resolving to nothing.

      Held near the saturation of the brand navy rather than at full strength. A
      pure red for blood — the largest system, so the most frequent colour on
      screen — fought with everything around it; these sit against the page's
      slate and teal instead of shouting over it. */
  color: string;
};

type FamilyRule = TissueFamily & { keywords: string[] };

// Order matters: the first match wins. Tumour sits above the organ systems on
// purpose — "lung carcinoma" is more usefully grouped with other tumours than
// with healthy lung, because that is the distinction someone browsing is drawing.
const FAMILY_RULES: FamilyRule[] = [
  {
    key: "tumour",
    label: "Tumour",
    color: "#c8638b",
    keywords: [
      "tumor",
      "tumour",
      "cancer",
      "carcinoma",
      "glioblastoma",
      "melanoma",
      "metasta",
      "sarcoma",
      "lymphoma",
      "leukemia",
      "leukaemia",
      "myeloma",
      "neoplas",
      "adenoma",
      "blastoma",
    ],
  },
  {
    key: "blood",
    label: "Blood & immune",
    color: "#3471c9",
    keywords: [
      "blood",
      "pbmc",
      "marrow",
      "lymph",
      "spleen",
      "thymus",
      "leukocyte",
      "monocyte",
      "lymphocyte",
      "platelet",
      "cd4",
      "cd8",
      "t cell",
      "b cell",
      "nk cell",
      "myeloid",
      "macrophage",
      "dendritic",
      "erythro",
    ],
  },
  {
    key: "nervous",
    label: "Nervous system",
    color: "#7b65cd",
    keywords: [
      "brain",
      "cortex",
      "neuron",
      "spinal",
      "cerebell",
      "hippocamp",
      "glia",
      "astrocyte",
      "nerve",
      "retina",
      "olfactory",
    ],
  },
  {
    key: "digestive",
    label: "Digestive",
    color: "#2dbeae",
    keywords: [
      "liver",
      "hepato",
      "intestin",
      "colon",
      "gut",
      "stomach",
      "gastric",
      "pancrea",
      "esophag",
      "bile",
      "duoden",
      "ileum",
      "rectal",
    ],
  },
  {
    key: "reproductive",
    label: "Reproductive & development",
    color: "#cb6795",
    keywords: [
      "ovary",
      "ovarian",
      "testis",
      "testicular",
      "uterus",
      "uterine",
      "placenta",
      "embryo",
      "prostate",
      "breast",
      "mammary",
      "endometri",
      "fetal",
      "blastocyst",
    ],
  },
  {
    key: "respiratory",
    label: "Respiratory",
    color: "#5aacd8",
    keywords: [
      "lung",
      "airway",
      "bronch",
      "trachea",
      "alveol",
      "nasal",
      "pulmonary",
    ],
  },
  {
    key: "urinary",
    label: "Kidney & urinary",
    color: "#86b957",
    keywords: ["kidney", "renal", "bladder", "urothel", "nephron"],
  },
  {
    key: "skin",
    label: "Skin",
    color: "#d8a25a",
    keywords: [
      "skin",
      "epiderm",
      "dermis",
      "dermal",
      "keratinocyte",
      "melanocyte",
    ],
  },
  {
    key: "muscle",
    label: "Heart & muscle",
    color: "#6f9e97",
    keywords: ["heart", "cardiac", "myocard", "muscle", "myotube", "myoblast"],
  },
  {
    key: "cultured",
    label: "Cultured cells",
    color: "#8398af",
    keywords: [
      "cell line",
      "ipsc",
      "hipsc",
      " esc",
      "organoid",
      "hek",
      "hela",
      "k562",
      "culture",
      "in vitro",
    ],
  },
];

/** Shown when the tissue is missing or matches no family. */
export const UNKNOWN_TISSUE_FAMILY: TissueFamily = {
  key: "unknown",
  label: "Unclassified tissue",
  color: "#e2e8f0",
};

// Shades of a system's colour
//
// A system declares one colour and the rest are derived from it, so adding a
// system or retuning one means editing a single hex rather than keeping three
// in step. Both derivations keep the hue exactly and move only lightness and
// saturation, which is what makes a pale block and the text on it read as the
// same colour rather than as two.

type Hsl = { h: number; s: number; l: number };

function hexToHsl(hex: string): Hsl {
  const n = parseInt(hex.slice(1), 16);
  const r = ((n >> 16) & 255) / 255;
  const g = ((n >> 8) & 255) / 255;
  const b = (n & 255) / 255;

  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };

  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h =
    max === r
      ? ((g - b) / d + (g < b ? 6 : 0)) / 6
      : max === g
        ? ((b - r) / d + 2) / 6
        : ((r - g) / d + 4) / 6;
  return { h, s, l };
}

function hslToHex({ h, s, l }: Hsl): string {
  const f = (n: number) => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    const v = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(v * 255)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** The system's colour as a pale wash, for a surface its own text sits on. */
export function tintOf(color: string): string {
  const { h, s } = hexToHsl(color);
  // Saturation is pulled back as lightness rises: a pastel that keeps full
  // saturation reads as a tinted highlight rather than as a calm surface.
  return hslToHex({ h, s: Math.min(s * 0.75, 0.6), l: 0.93 });
}

/** The system's colour darkened enough to read on its own tint. */
export function inkOf(color: string): string {
  const { h, s } = hexToHsl(color);
  // Saturation goes up as it darkens so the hue survives; the near-grey systems
  // stay near-grey because a proportional rise leaves them there.
  return hslToHex({ h, s: Math.min(s * 1.15, 0.7), l: 0.32 });
}

export function tissueFamily(tissue?: string | null): TissueFamily {
  if (!tissue) return UNKNOWN_TISSUE_FAMILY;
  const text = tissue.toLowerCase();
  const rule = FAMILY_RULES.find((f) =>
    f.keywords.some((k) => text.includes(k)),
  );
  if (!rule) return UNKNOWN_TISSUE_FAMILY;
  return { key: rule.key, label: rule.label, color: rule.color };
}

/** The same ten systems and the unclassified one, in each palette's own
 *  materials.
 *
 * A set per palette rather than a rule applied to one set. What has to hold for
 * a categorical palette is that the systems stay apart from each other, and any
 * rule that pulled every hue toward a scheme would have pulled the four that are
 * already neighbours on top of one another. So each system keeps its job and
 * changes what it is made of.
 *
 * Warm and cool are balanced inside each set, because a card is one of these
 * against the page and a grid of cards is all of them at once. The warm scheme
 * gets its separation from three greens; the cool one from a mango and a coral,
 * which are the two warm notes it is allowed.
 *
 * Keyed rather than ordered, so that adding a system to the rules above and
 * forgetting it here falls back to its default colour instead of silently
 * shifting every family after it along by one. */
const FAMILY_SETS: Record<string, Record<string, string>> = {
  nautical: {
    tumour: "#b2472a",
    blood: "#8c2f2f",
    nervous: "#8a6d3b",
    digestive: "#6d8a35",
    reproductive: "#c06a4a",
    respiratory: "#4f7a4a",
    urinary: "#93a02f",
    skin: "#c99a4e",
    muscle: "#7a5a3f",
    cultured: "#a89880",
    unknown: "#e2d3b4",
  },
  night: {
    tumour: "#ef5f8f",
    blood: "#7d8cf0",
    nervous: "#a06ee8",
    digestive: "#4fc8c0",
    reproductive: "#f07a5a",
    respiratory: "#5aa8ee",
    urinary: "#7ed08f",
    skin: "#f0c266",
    muscle: "#6fa8b8",
    cultured: "#8a86ac",
    unknown: "#302a58",
  },
  tropical: {
    tumour: "#d4741f",
    blood: "#1f6fa8",
    nervous: "#5a63b8",
    digestive: "#12a08c",
    reproductive: "#e0665a",
    respiratory: "#35a7d8",
    urinary: "#4faa4a",
    skin: "#e0b23a",
    muscle: "#2f8f83",
    cultured: "#7fa8b0",
    unknown: "#cfe6dd",
  },
};

/** A family's colour under a given palette.
 *
 * Separate from `tissueFamily` because which system a tissue belongs to is a
 * fact about the tissue, and what colour that system is drawn in is a fact about
 * the page. Only the second changes when the reader picks a theme.
 *
 * The palette has to be passed in rather than read here: this module is reached
 * from render, and the wash and the ink are computed from the hue by the code
 * below, which cannot be handed a CSS variable. Everything else a palette
 * answers is a custom property in globals.css; these ten are the exception, and
 * this is what the exception costs. */
export function familyColor(family: TissueFamily, palette: string): string {
  return FAMILY_SETS[palette]?.[family.key] ?? family.color;
}

/** Every family, for a legend or a key. */
export const TISSUE_FAMILIES: TissueFamily[] = FAMILY_RULES.map(
  ({ key, label, color }) => ({
    key,
    label,
    color,
  }),
);

// Turns a concept's declared palette (plain words in concepts.json, e.g.
// "deep indigo", "silver mist") into CSS colours for catalogue art. Purely
// decorative: the words are the channel's preference, the gradient only
// illustrates it. Unknown words fall back to a stable hue from their text.
const NAMED: Array<[RegExp, string]> = [
  [/deep indigo|indigo/, "#3b41a8"],
  [/sapphire/, "#2f5fd0"],
  [/violet/, "#7b5cff"],
  [/aqua/, "#3fc6d8"],
  [/teal/, "#2bb39a"],
  [/mist|silver/, "#aeb8d0"],
  [/moon/, "#dbe3ff"],
  [/pale stone|stone/, "#cfc9bd"],
  [/amber|brass|warm|lamp/, "#c98a3a"],
  [/glow/, "#5fe3d0"],
  [/white/, "#e8ecf5"],
];

function hashHue(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return h % 360;
}

export function paletteColor(word: string): string {
  const w = word.toLowerCase();
  for (const [re, color] of NAMED) if (re.test(w)) return color;
  return `hsl(${hashHue(w)} 45% 55%)`;
}

export function paletteGradient(palette: string[] | undefined, seed = ""): string {
  const words = palette && palette.length > 0 ? palette : ["deep indigo", "aqua", "violet"];
  const colors = words.slice(0, 4).map(paletteColor);
  const angle = 160 + (hashHue(seed) % 50);
  const stops = colors.map((c, i) => `${c} ${Math.round((i / Math.max(colors.length - 1, 1)) * 100)}%`);
  return `linear-gradient(${angle}deg, ${stops.join(", ")})`;
}

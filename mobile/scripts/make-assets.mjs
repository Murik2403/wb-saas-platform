// Builds icon/splash sources from the existing brand SVG (mh-redesign-lab/public/icons/marketshelper.svg).
import sharp from "sharp";
import { readFileSync } from "node:fs";

const svgPath = new URL("../../mh-redesign-lab/public/icons/marketshelper.svg", import.meta.url);
const svg = readFileSync(svgPath, "utf8");
const BG = "#0b1115";
// Mark only (the "H" with two dots), without the rounded tile: 154..358 x 137..375 of the 512 viewBox.
const markSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="100 100 312 312">${svg.match(/<path[\s\S]*<\/svg>/)[0]}`;
const render = (s, size) => sharp(Buffer.from(s), { density: 300 }).resize(size, size).png().toBuffer();
const out = (n) => new URL("../assets/" + n, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

async function onBg(size, bg, logoSvg, logoSize, file, flat = true) {
  const logo = await render(logoSvg, logoSize);
  let img = sharp({ create: { width: size, height: size, channels: 4, background: bg } }).composite([{ input: logo, gravity: "center" }]);
  if (flat) img = img.flatten({ background: bg }).removeAlpha();
  await img.png().toFile(file);
}

await onBg(1024, BG, svg, 600, out("icon-only.png"));                     // 58%: >= 20% margin
await onBg(1024, "#00000000", markSvg, 620, out("icon-foreground.png"), false); // adaptive foreground, inside 66% safe zone
await onBg(1024, BG, "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'/>", 8, out("icon-background.png"));
await onBg(2732, BG, svg, 720, out("splash.png"));
await onBg(2732, BG, svg, 720, out("splash-dark.png"));
// PWA maskable 512: full-bleed tile colour, logo inside the 80% safe circle.
await onBg(512, "#08110f", svg, 300, new URL("../../mh-redesign-lab/public/icons/icon-maskable-512.png", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
console.log("assets ok");

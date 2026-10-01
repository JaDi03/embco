// Generates PWA/app icons from the SVGs in public/brand.
// Usage: npm run icons
import { copyFile } from "node:fs/promises";
import sharp from "sharp";

const LOGO = "public/brand/logo.svg";
const MASKABLE = "public/brand/logo-maskable.svg";

const render = (src, size, out, flatten = false) => {
  let img = sharp(src, { density: 384 }).resize(size, size);
  // iOS fills transparent apple-touch-icon pixels with black, so flatten onto the brand color
  if (flatten) img = img.flatten({ background: "#005ddb" });
  return img.png().toFile(out);
};

await Promise.all([
  render(LOGO, 192, "public/icons/icon-192.png"),
  render(LOGO, 512, "public/icons/icon-512.png"),
  render(MASKABLE, 512, "public/icons/maskable-512.png"),
  render(MASKABLE, 180, "src/app/apple-icon.png", true),
  copyFile(LOGO, "src/app/icon.svg"),
]);

console.log("Icons generated.");

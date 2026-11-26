import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";

await mkdir("dist/assets", { recursive: true });
await build({
  entryPoints: ["src/app.js"],
  outfile: "dist/assets/app.js",
  bundle: true,
  format: "esm",
  minify: true,
  target: ["es2022"],
  legalComments: "linked",
});
await build({
  entryPoints: ["src/style.css"],
  outfile: "dist/assets/style.css",
  bundle: true,
  minify: true,
  loader: { ".png": "dataurl" },
});
await copyFile("src/bootstrap.js", "dist/assets/bootstrap.js");
await copyFile("src/trends_earth_logo.png", "dist/assets/trends_earth_logo.png");

// Basemaps, national borders and labels shared with Resilience Atlas (resilienceatlas
// frontend/src/views/utils/basemaps.json, labels.json and OLMap/boundaries.ts).
import TileLayer from "ol/layer/Tile.js";
import VectorTileLayer from "ol/layer/VectorTile.js";
import XYZ from "ol/source/XYZ.js";
import VectorTileSource from "ol/source/VectorTile.js";
import MVT from "ol/format/MVT.js";
import { Stroke, Style } from "ol/style.js";

export const MAPBOX_ORIGIN = "https://api.mapbox.com";
const MAPBOX_TOKEN =
  "pk.eyJ1IjoiY2lncnAiLCJhIjoiYTQ5YzVmYTk4YzM0ZWM4OTU1ZjQxMWI5ZDNiNTQ5M2IifQ.SBgo9jJftBDx4c5gX4wm3g";

export const BASEMAPS = {
  defaultmap: { label: "Default", style: "cixkh6jb000582smx8pfdeu23", borders: "dark" },
  satellite: { label: "Satellite", style: "cizsz6pv700422ro73xdhzi1g", borders: "light" },
  topographic: { label: "Topographic", style: "clgmd4mqr00ci01r7eh0ngxkz", borders: "dark" },
  dark: { label: "Dark", style: "cixtef50400162rla1jtwtoyi", borders: "light" },
};
const LABEL_STYLES = { light: "clgyr7y4u009l01qt32300lx9", dark: "clgyh74fy00g901qy2bmmerq9" };
export const DEFAULT_BASEMAP = "defaultmap";

const BORDER_COLORS = {
  light: { halo: "#ffffff59", line: "#f0f0f0b3" },
  dark: { halo: "#00000059", line: "#666666b3" },
};

function styleUrl(style) {
  return `${MAPBOX_ORIGIN}/styles/v1/cigrp/${style}/tiles/256/{z}/{x}/{y}@2x?access_token=${MAPBOX_TOKEN}`;
}

function basemapUrl(key) {
  return styleUrl(BASEMAPS[key].style);
}

export function createBasemapLayer(key = DEFAULT_BASEMAP) {
  return new TileLayer({
    source: new XYZ({
      url: basemapUrl(key), crossOrigin: "anonymous", tilePixelRatio: 2,
      tileSize: 256, maxZoom: 22,
    }),
    zIndex: -1,
    className: "basemap",
    properties: { basemap: key },
  });
}

export function setBasemap(layer, key) {
  layer.setSource(createBasemapLayer(key).getSource());
  layer.set("basemap", key);
}

// National (admin level 0), non-maritime borders drawn above every data layer.
export function createBorderLayer(tone = "dark") {
  const styles = {};
  for (const [name, colors] of Object.entries(BORDER_COLORS)) {
    styles[name] = [
      new Style({ stroke: new Stroke({ color: colors.halo, width: 2.5 }) }),
      new Style({ stroke: new Stroke({ color: colors.line, width: 1.2 }) }),
    ];
  }
  const layer = new VectorTileLayer({
    source: new VectorTileSource({
      url: `${MAPBOX_ORIGIN}/v4/mapbox.mapbox-streets-v8/{z}/{x}/{y}.mvt?access_token=${MAPBOX_TOKEN}`,
      format: new MVT({ layers: ["admin"] }),
      maxZoom: 14,
    }),
    style: (feature) => {
      const maritime = feature.get("maritime");
      if (Number(feature.get("admin_level")) !== 0 || maritime === 1 || maritime === true ||
          maritime === "true") return null;
      return styles[layer.get("tone")];
    },
    zIndex: 100000,
    className: "national-borders",
    declutter: false,
    properties: { tone },
  });
  return layer;
}

function labelSource(tone) {
  return new XYZ({
    url: styleUrl(LABEL_STYLES[tone]), crossOrigin: "anonymous", tilePixelRatio: 2,
    tileSize: 256, maxZoom: 22,
  });
}

// Place-name labels drawn above the borders; hidden until the viewer turns them on.
export function createLabelLayer(tone = "dark") {
  return new TileLayer({
    source: labelSource(tone),
    zIndex: 100001,
    className: "map-labels",
    visible: false,
    properties: { tone },
  });
}

export function setLabelTone(layer, tone) {
  if (layer.get("tone") === tone) return;
  layer.setSource(labelSource(tone));
  layer.set("tone", tone);
}

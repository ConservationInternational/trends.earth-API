import Map from "ol/Map.js";
import View from "ol/View.js";
import TileLayer from "ol/layer/Tile.js";
import VectorTileLayer from "ol/layer/VectorTile.js";
import XYZ from "ol/source/XYZ.js";
import VectorTileSource from "ol/source/VectorTile.js";
import MVT from "ol/format/MVT.js";
import GeoJSON from "ol/format/GeoJSON.js";
import Feature from "ol/Feature.js";
import TileState from "ol/TileState.js";
import { Circle, Fill, Stroke, Style } from "ol/style.js";
import { fromLonLat, transformExtent } from "ol/proj.js";
import { createEmpty, extend } from "ol/extent.js";
import { defaults as defaultControls } from "ol/control.js";
import { createXYZ } from "ol/tilegrid.js";
import { getRenderPixel } from "ol/render.js";
import booleanIntersects from "@turf/boolean-intersects";
import {
  BASEMAPS, DEFAULT_BASEMAP, createBasemapLayer, createBorderLayer, setBasemap,
} from "./basemaps.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const RGB = /^#[0-9a-f]{6}$/i;
const PALETTES = {
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  plasma: ["#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"],
  magma: ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
  inferno: ["#000004", "#57106e", "#bc3754", "#f98e09", "#fcffa4"],
  cividis: ["#00224e", "#434e6c", "#7d7c78", "#bcae6c", "#fee838"],
  turbo: ["#30123b", "#28bbec", "#a4fc3c", "#fb7e21", "#7a0403"],
};
const MAX_TILE_BYTES = 2 * 1024 * 1024;
const geojson = new GeoJSON();
const $ = (id) => document.getElementById(id);

class ViewerError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

class MaskError extends ViewerError {
  constructor() { super(502, "The map contains an invalid mask."); }
}

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function icon(path) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const shape = document.createElementNS(SVG_NS, "path");
  shape.setAttribute("d", path);
  svg.append(shape);
  return svg;
}
const GRIP_ICON = "M5 3h2v2H5zm4 0h2v2H9zM5 7h2v2H5zm4 0h2v2H9zm-4 4h2v2H5zm4 0h2v2H9z";
const CHEVRON_ICON = "M4.5 6 8 9.5 11.5 6 13 7.5l-5 5-5-5z";

function validateManifest(data, shareId, token) {
  if (!data || !UUID.test(data.id) || (shareId && data.id !== shareId) || typeof data.title !== "string" ||
      !Number.isFinite(Date.parse(data.expires_at)) || !Array.isArray(data.layers)) {
    throw new ViewerError(502, "The map manifest is invalid.");
  }
  const ids = new Set();
  if (data.mask !== undefined && data.mask !== null) {
    const mask = data.mask;
    if (!mask || !["Polygon", "MultiPolygon"].includes(mask.type) ||
        !Array.isArray(mask.coordinates) || !Number.isInteger(data.mask_version) ||
        data.mask_version < 0) {
      throw new MaskError();
    }
    const polygons = mask.type === "Polygon" ? [mask.coordinates] : mask.coordinates;
    let count = 0;
    if (!polygons.length || !polygons.every((polygon) =>
      Array.isArray(polygon) && polygon.length && polygon.every((ring) => {
        if (!Array.isArray(ring) || ring.length < 4) return false;
        count += ring.length;
        return ring.every((point) => Array.isArray(point) && point.length === 2 &&
          point.every(Number.isFinite) && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90) &&
          ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1];
      })) || count > 30000) {
      throw new MaskError();
    }
  } else if (data.mask_version !== undefined &&
      (!Number.isInteger(data.mask_version) || data.mask_version < 0)) {
    throw new MaskError();
  }
  for (const layer of data.layers) {
    const extension = layer.kind === "vector" ? "pbf" : "png";
    const tileBase = token ? `/api/v1/public-shares/tiles/${data.id}/${token}` :
      `/api/v1/public-shares/${shareId}/tiles`;
    const expected = `${tileBase}/${layer.id}/{z}/{x}/{y}.${extension}`;
    if (!UUID.test(layer.id) || ids.has(layer.id) || !["raster", "vector"].includes(layer.kind) ||
        layer.tile_format !== extension || layer.tiles !== expected ||
        typeof layer.name !== "string" || typeof layer.attribution !== "string" ||
        !Array.isArray(layer.bounds) || layer.bounds.length !== 4 ||
        !layer.bounds.every(Number.isFinite) ||
        layer.bounds[0] > layer.bounds[2] || layer.bounds[1] > layer.bounds[3]) {
      throw new ViewerError(502, "The map contains invalid layer metadata.");
    }
    ids.add(layer.id);
    const style = layer.style;
    if (!style || !["continuous", "categorical", "vector"].includes(style.kind)) {
      throw new ViewerError(502, "The map contains an unsupported style.");
    }
    if (layer.kind === "vector") {
      const metadata = layer.vector_metadata;
      if (style.kind !== "vector" || !RGB.test(style.stroke) || !RGB.test(style.fill) ||
          !metadata || typeof metadata.source_layer !== "string" ||
          !Number.isInteger(metadata.minzoom) || !Number.isInteger(metadata.maxzoom) ||
          metadata.minzoom < 0 || metadata.maxzoom > 22 || metadata.minzoom > metadata.maxzoom ||
          ![style.width, style.fill_opacity, style.radius].every(Number.isFinite) ||
          style.width < 0 || style.width > 10 || style.fill_opacity < 0 ||
          style.fill_opacity > 1 || style.radius < 0 || style.radius > 20 ||
          (style.category && typeof style.category.field !== "string")) {
        throw new ViewerError(502, "The map contains invalid vector metadata.");
      }
    } else if (style.kind === "continuous") {
      const validRange = [style.min, style.max].every(Number.isFinite) && style.min < style.max;
      const hasPalette = Object.hasOwn(PALETTES, style.palette) &&
        Object.keys(style).sort().join(",") === "kind,max,min,palette";
      const hasStops = Array.isArray(style.stops) && style.stops.length >= 2 &&
        style.stops.length <= 32 &&
        Object.keys(style).sort().join(",") === "kind,max,min,stops" &&
        style.stops.every((stop, index) => stop && Number.isFinite(stop.value) &&
          RGB.test(stop.color) && stop.value >= style.min && stop.value <= style.max &&
          (index === 0 || stop.value > style.stops[index - 1].value)) &&
        style.stops[0].value === style.min &&
        style.stops.at(-1).value === style.max;
      if (!validRange || (!hasPalette && !hasStops)) {
        throw new ViewerError(502, "The map contains invalid raster metadata.");
      }
    } else if (style.kind !== "categorical") {
      throw new ViewerError(502, "The map contains invalid raster metadata.");
    }
    const values = style.kind === "categorical" ? style.values : style.category?.values;
    if (values && (typeof values !== "object" || Object.keys(values).length > 256 ||
        !Object.values(values).every((color) => typeof color === "string" && RGB.test(color)))) {
      throw new ViewerError(502, "The map contains invalid legend colors.");
    }
    const labels = style.kind === "categorical" ? style.labels : style.category?.labels;
    const stopLabels = style.stops?.filter((stop) => stop.label !== undefined).map((stop) => stop.label) || [];
    if ((style.category?.other_label !== undefined && !validLabel(style.category.other_label)) ||
        (labels !== undefined && (!labels || typeof labels !== "object" ||
        !Object.keys(labels).every((key) => Object.hasOwn(values || {}, key)))) ||
        ![...Object.values(labels || {}), ...stopLabels].every(validLabel)) {
      throw new ViewerError(502, "The map contains invalid legend labels.");
    }
  }
  return data;
}

function validLabel(label) {
  return typeof label === "string" && label.length >= 1 && label.length <= 200;
}

function legendList(entries) {
  const list = document.createElement("ul");
  list.className = "legend";
  for (const [label, color] of entries) {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.backgroundColor = color;
    swatch.setAttribute("aria-hidden", "true");
    item.append(swatch, textElement("span", label));
    list.append(item);
  }
  return list;
}

function legend(layer) {
  const container = document.createElement("div");
  const style = layer.style;
  if (style.kind === "continuous") {
    const gradient = document.createElement("div");
    gradient.className = "gradient";
    const colors = style.stops ?
      style.stops.map((stop) =>
        `${stop.color} ${100 * (stop.value - style.min) / (style.max - style.min)}%`,
      ) : PALETTES[style.palette];
    gradient.style.background = `linear-gradient(to right, ${colors.join(",")})`;
    gradient.setAttribute("aria-label", `Color ramp from ${style.min} to ${style.max}`);
    container.append(gradient);
    const labelled = (style.stops || []).filter((stop) => stop.label);
    if (labelled.length) {
      container.append(legendList(labelled.map((stop) => [stop.label, stop.color])));
    } else {
      const range = document.createElement("div");
      range.className = "legend-range";
      range.append(textElement("span", style.min), textElement("span", style.max));
      container.append(range);
    }
    return container;
  }
  const categories = style.kind === "categorical" ? style.values : style.category?.values;
  const labels = (style.kind === "categorical" ? style.labels : style.category?.labels) || {};
  const ordered = categories ? Object.entries(categories) : [];
  if (style.kind === "categorical") ordered.sort(([a], [b]) => Number(a) - Number(b));
  const entries = categories ?
    ordered.map(([value, color]) => [labels[value] || value, color]) :
    [[layer.name, style.fill]];
  if (style.category?.other_label) entries.push([style.category.other_label, style.fill]);
  container.append(legendList(entries));
  return container;
}

export async function start(initialSecret, hadFragment) {
  const map = new Map({
    target: "map",
    maxTilesLoading: 6,
    controls: defaultControls({ attribution: false, rotate: false }),
    view: new View({ center: fromLonLat([0, 0]), zoom: 2, minZoom: 0, maxZoom: 22 }),
  });
  $("map").setAttribute("aria-label", "Interactive map. Arrow keys pan; plus and minus zoom.");
  const match = location.pathname.match(/^\/share\/([^/]+)$/);
  const shareId = match?.[1];
  const token = location.pathname.match(/^\/share\/s\/([A-Za-z0-9_-]{32,128})$/)?.[1];
  let manifest = null;
  let maskGeometry = null;
  let maskFeature = null;
  let records = [];
  // Viewer-only layer order chosen by dragging; it never changes the share.
  let viewerOrder = null;
  let stopped = false;
  let busy = false;
  let timer = null;
  let expiryTimer = null;
  let sessionDeadline = 0;
  const pending = new Set();
  const tileQueue = [];
  let activeTiles = 0;
  const base = `/api/v1/public-shares/${token ? `s/${token}` : shareId}`;
  const basemap = createBasemapLayer(DEFAULT_BASEMAP);
  const borders = createBorderLayer(BASEMAPS[DEFAULT_BASEMAP].borders);
  // Small screens show the map full screen; the controls open as an overlay sheet.
  const compact = window.matchMedia("(max-width: 720px), (max-height: 500px)");
  let tocOpen = false;

  function syncToc() {
    const sheet = compact.matches;
    document.body.classList.toggle("toc-open", sheet && tocOpen);
    $("controls").inert = sheet && !tocOpen;
    $("map-region").inert = sheet && tocOpen;
    for (const id of ["toc-open", "toc-close"]) {
      $(id).setAttribute("aria-expanded", String(!sheet || tocOpen));
    }
  }

  function setToc(open, { focus = true } = {}) {
    if (tocOpen === open) return;
    tocOpen = open;
    if (open) $("mobile-notice").hidden = true;
    syncToc();
    if (compact.matches && focus) $(open ? "toc-close" : "toc-open").focus();
  }

  function status(message, state) {
    $("status").textContent = message;
    $("status").dataset.state = state;
    $("status").setAttribute("role", state === "error" ? "alert" : "status");
    const attention = Boolean(message) && ["warning", "error"].includes(state);
    $("toc-open").classList.toggle("attention", attention);
    $("toc-open").setAttribute("aria-label", attention ? "Layers (has a status message)" : "Layers");
  }

  function clearLayers() {
    map.removeLayer(basemap);
    map.removeLayer(borders);
    for (const record of records) {
      map.removeLayer(record.grid);
      record.grid.getSource().clear();
    }
    records = [];
    $("layers").replaceChildren();
    $("attributes").replaceChildren();
    $("selection").hidden = true;
  }

  function stop(message) {
    stopped = true;
    clearTimeout(timer);
    clearTimeout(expiryTimer);
    for (const controller of pending) controller.abort();
    clearLayers();
    $("map-tools").hidden = true;
    $("map-placeholder").hidden = false;
    $("map-placeholder").textContent = message;
    $("retry").hidden = true;
    map.getInteractions().forEach((interaction) => interaction.setActive(false));
    status(message, "error");
  }

  function enforceExpiry() {
    clearTimeout(expiryTimer);
    if (stopped || !manifest) return;
    const shareExpiry = Date.parse(manifest.expires_at);
    const deadline = token ? shareExpiry : Math.min(shareExpiry, sessionDeadline);
    if (Date.now() >= deadline) {
      stop(shareExpiry <= Date.now() ? "This shared map has expired." :
        "This viewer session has expired. Open the original current link to authorize again.");
      return;
    }
    expiryTimer = setTimeout(enforceExpiry, Math.min(deadline - Date.now(), 2147483647));
  }

  function failure(error) {
    if (stopped || error.name === "AbortError") return;
    if ([401, 403, 404].includes(error.status)) {
      stop("This share or session is unavailable. The link may be invalid, expired, rotated, or revoked. Open the original current link to authorize again.");
    } else {
      status("Map service temporarily unavailable. Some content may be missing. Retry loading the map.", "warning");
      $("retry").hidden = false;
    }
  }

  async function request(path, { method = "GET", body, tile = false } = {}) {
    const controller = new AbortController();
    pending.add(controller);
    const timeout = setTimeout(() => controller.abort(), tile ? 45000 : 15000);
    try {
      const response = await fetch(path, {
        method, body: body ? JSON.stringify(body) : undefined,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        credentials: "same-origin", cache: "no-store", redirect: "error",
        referrerPolicy: "no-referrer", signal: controller.signal,
      });
      if (!response.ok) {
        const error = new ViewerError(response.status, "Map request failed.");
        const retryAfter = response.headers.get("Retry-After");
        const seconds = Number(retryAfter);
        error.retryAfter = Math.max(0, Math.min(30000, Number.isFinite(seconds) ?
          seconds * 1000 : Date.parse(retryAfter) - Date.now())) || 0;
        throw error;
      }
      if (tile && response.status === 204) return null;
      const type = response.headers.get("Content-Type")?.split(";")[0];
      const expected = tile ?
        ["image/png", "application/x-protobuf", "application/vnd.mapbox-vector-tile"] :
        ["application/json"];
      if (!expected.includes(type)) throw new ViewerError(502, "Invalid map response.");
      const limit = tile ? MAX_TILE_BYTES : 2 * 1024 * 1024;
      const reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > limit) {
          await reader.cancel();
          throw new ViewerError(502, "Map response exceeds its size limit.");
        }
        chunks.push(value);
      }
      const data = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.length; }
      return tile ? { data, type } : JSON.parse(new TextDecoder().decode(data));
    } catch (error) {
      if (error.name === "AbortError" && !stopped) {
        throw new ViewerError(504, "Map request timed out.");
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      pending.delete(controller);
    }
  }

  async function requestTile(url, isCurrent) {
    if (activeTiles >= 6) await new Promise((resolve) => tileQueue.push(resolve));
    else activeTiles++;
    try {
      for (let attempt = 0; ; attempt++) {
        if (stopped || !isCurrent()) return null;
        try {
          return await request(url, { tile: true });
        } catch (error) {
          const retryable = [429, 502, 503, 504].includes(error.status) || error instanceof TypeError;
          if (stopped || !isCurrent() || !retryable || attempt >= 2) throw error;
          const delay = Math.max(error.retryAfter || 0, 500 * 2 ** attempt * (1 + Math.random()));
          await new Promise((resolve) => setTimeout(resolve, delay));
        }
      }
    } finally {
      const resume = tileQueue.shift();
      if (resume) resume();
      else activeTiles--;
    }
  }

  function selectFeature(feature, layerName) {
    const reveal = compact.matches && !tocOpen;
    $("selection").hidden = false;
    $("attributes").replaceChildren(textElement("dt", "Layer"), textElement("dd", layerName));
    const properties = { ...feature.getProperties() };
    delete properties[feature.getGeometryName()];
    for (const [name, value] of Object.entries(properties)) {
      $("attributes").append(textElement("dt", name), textElement("dd", value));
    }
    if (!Object.keys(properties).length) {
      $("attributes").append(textElement("dt", "Attributes"), textElement("dd", "No attributes embedded."));
    }
    if (reveal) {
      setToc(true, { focus: false });
      $("selection-title").focus({ preventScroll: true });
    }
    $("selection").scrollIntoView({ block: "nearest" });
  }

  function setMask(mask) {
    try {
      maskGeometry = mask ? geojson.readGeometry(mask, {
        dataProjection: "EPSG:4326", featureProjection: "EPSG:3857",
      }) : null;
      maskFeature = maskGeometry ? {
        type: "Feature", properties: {}, geometry: geojson.writeGeometryObject(maskGeometry),
      } : null;
    } catch (error) {
      throw new MaskError();
    }
    $("feature-picker")?.remove();
    $("attributes").replaceChildren();
    $("selection").hidden = true;
    for (const record of records) record.grid.changed();
  }

  function clipLayer(grid) {
    let maskSaved = false;
    grid.on("prerender", (event) => {
      if (!maskGeometry) return;
      const context = event.context;
      if (!(context instanceof CanvasRenderingContext2D)) {
        stop("The display mask cannot be rendered by this browser.");
        return;
      }
      const polygons = maskGeometry.getType() === "Polygon" ?
        [maskGeometry.getCoordinates()] : maskGeometry.getCoordinates();
      context.save();
      context.beginPath();
      for (const polygon of polygons) for (const ring of polygon) {
        ring.forEach((coordinate, index) => {
          const pixel = getRenderPixel(event, map.getPixelFromCoordinate(coordinate));
          if (index === 0) context.moveTo(...pixel);
          else context.lineTo(...pixel);
        });
        context.closePath();
      }
      context.clip("evenodd");
      maskSaved = true;
    });
    grid.on("postrender", (event) => {
      if (maskSaved) {
        event.context.restore();
        maskSaved = false;
      }
    });
  }

  function makeGrid(layer, zIndex) {
    const vector = layer.kind === "vector";
    const extent = transformExtent([
      layer.bounds[0], Math.max(-85.051129, layer.bounds[1]),
      layer.bounds[2], Math.min(85.051129, layer.bounds[3]),
    ], "EPSG:4326", "EPSG:3857");
    let grid;
    async function loadTile(tile, url, tileExtent, projection) {
      try {
        const result = await requestTile(url, () => map.getLayers().getArray().includes(grid));
        if (stopped || !map.getLayers().getArray().includes(grid)) {
          tile.setState(TileState.EMPTY);
          return;
        }
        if (!result) {
          if (vector) tile.setFeatures([]);
          else tile.setState(TileState.EMPTY);
          return;
        }
        if (vector) {
          if (!["application/x-protobuf", "application/vnd.mapbox-vector-tile"].includes(result.type)) {
            throw new ViewerError(502, "Invalid vector tile.");
          }
          const features = tile.getFormat().readFeatures(result.data.buffer, {
            extent: tileExtent, featureProjection: projection,
          });
          tile.setFeatures(features);
        } else {
          if (result.type !== "image/png") throw new ViewerError(502, "Invalid raster tile.");
          const image = tile.getImage();
          const blobUrl = URL.createObjectURL(new Blob([result.data], { type: "image/png" }));
          image.addEventListener("load", () => URL.revokeObjectURL(blobUrl), { once: true });
          image.addEventListener("error", () => {
            URL.revokeObjectURL(blobUrl);
            failure(new ViewerError(502, "Raster tile could not be decoded."));
          }, { once: true });
          image.src = blobUrl;
        }
      } catch (error) {
        tile.setState(TileState.ERROR);
        if (map.getLayers().getArray().includes(grid)) {
          if (error.status === 404) {
            await poll();
            if (map.getLayers().getArray().includes(grid)) {
              failure(new ViewerError(502, "Layer tile is unavailable."));
            }
          } else {
            failure(error);
          }
        }
      }
    }
    const sourceOptions = {
      url: layer.tiles, wrapX: false, tileSize: 256, transition: 0,
      minZoom: vector ? layer.vector_metadata.minzoom : 0,
      maxZoom: vector ? layer.vector_metadata.maxzoom : 22,
      tileLoadFunction: vector ?
        (tile, url) => tile.setLoader((tileExtent, resolution, projection) =>
          loadTile(tile, url, tileExtent, projection)) :
        (tile, url) => { loadTile(tile, url); },
    };
    const source = vector ? new VectorTileSource({
      ...sourceOptions, format: new MVT({
        featureClass: Feature, layers: [layer.vector_metadata.source_layer],
      }),
      tileGrid: createXYZ({
        tileSize: 256, minZoom: layer.vector_metadata.minzoom,
        maxZoom: layer.vector_metadata.maxzoom,
      }),
    }) : new XYZ(sourceOptions);
    const styleCache = new globalThis.Map();
    function vectorStyle(feature) {
      const category = layer.style.category;
      const key = category ? String(feature.get(category.field)) : "";
      const color = category && Object.hasOwn(category.values, key) ? category.values[key] : null;
      if (!styleCache.has(color)) {
        const fillHex = color || layer.style.fill;
        const rgb = [1, 3, 5].map((offset) => parseInt(fillHex.slice(offset, offset + 2), 16));
        const fill = new Fill({ color: [...rgb, layer.style.fill_opacity] });
        const stroke = new Stroke({ color: color || layer.style.stroke, width: layer.style.width });
        styleCache.set(color, new Style({
          fill, stroke,
          image: new Circle({ radius: layer.style.radius, fill, stroke }),
        }));
      }
      return styleCache.get(color);
    }
    grid = vector ? new VectorTileLayer({
      source, style: vectorStyle, extent, zIndex,
      className: `layer-${layer.id}`, renderMode: "vector",
    }) : new TileLayer({
      source, extent, zIndex, className: `layer-${layer.id}`,
    });
    clipLayer(grid);
    return grid;
  }

  function fit() {
    if (!manifest || !manifest.layers.length) return;
    const extent = createEmpty();
    for (const layer of manifest.layers) {
      extend(extent, transformExtent([
        layer.bounds[0], Math.max(-85.051129, layer.bounds[1]),
        layer.bounds[2], Math.min(85.051129, layer.bounds[3]),
      ], "EPSG:4326", "EPSG:3857"));
    }
    map.updateSize();
    map.getView().fit(extent, { padding: [20, 20, 20, 20], maxZoom: 12 });
  }

  function orderedLayers() {
    if (!viewerOrder) return manifest.layers;
    const rank = new globalThis.Map(viewerOrder.map((id, index) => [id, index]));
    const known = manifest.layers.filter((layer) => rank.has(layer.id))
      .sort((a, b) => rank.get(a.id) - rank.get(b.id));
    // Layers added since the viewer reordered keep their shared position.
    return manifest.layers.map((layer) => (rank.has(layer.id) ? known.shift() : layer));
  }

  function applyOrder(ids) {
    const byId = new globalThis.Map(records.map((record) => [record.layer.id, record]));
    if (ids.length !== records.length || !ids.every((id) => byId.has(id))) return;
    viewerOrder = ids;
    records = ids.map((id) => byId.get(id));
    records.forEach((record, index) => {
      record.grid.setZIndex(records.length - index);
      $("layers").append(record.card);
    });
  }

  function cardOrder() {
    return [...$("layers").querySelectorAll(".layer")].map((card) => card.dataset.layerId);
  }

  function startDrag(event, record) {
    if (event.button !== 0 || records.length < 2) return;
    event.preventDefault();
    const { pointerId } = event;
    const { card } = record;
    const listeners = new AbortController();
    card.classList.add("dragging");
    $("layers").classList.add("reordering");
    document.addEventListener("pointermove", (moveEvent) => {
      if (moveEvent.pointerId !== pointerId || !records.includes(record)) return;
      const next = [...$("layers").querySelectorAll(".layer")].find((other) => {
        if (other === card) return false;
        const rect = other.getBoundingClientRect();
        return moveEvent.clientY < rect.top + rect.height / 2;
      });
      if (next) $("layers").insertBefore(card, next);
      else $("layers").append(card);
    }, { signal: listeners.signal });
    const finish = () => {
      listeners.abort();
      card.classList.remove("dragging");
      $("layers").classList.remove("reordering");
      // A manifest update may have replaced the list during the drag.
      if (records.includes(record)) applyOrder(cardOrder());
    };
    for (const type of ["pointerup", "pointercancel"]) {
      document.addEventListener(type, (endEvent) => {
        if (endEvent.pointerId === pointerId) finish();
      }, { signal: listeners.signal });
    }
  }

  function moveBy(record, offset) {
    const ids = records.map((item) => item.layer.id);
    const from = ids.indexOf(record.layer.id);
    const to = from + offset;
    if (from < 0 || to < 0 || to >= ids.length) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    applyOrder(ids);
    record.card.querySelector(".drag-handle").focus();
  }

  function render(fitLayers = true) {
    const previous = new globalThis.Map(records.map(({ layer, grid, card }) => [
      layer.id, {
        visible: grid.getVisible(),
        opacity: grid.getOpacity(),
        collapsed: card.classList.contains("collapsed"),
      },
    ]));
    clearLayers();
    $("title").textContent = manifest.title;
    // Keep browser tab titles generic: uploaded names may contain sensitive text.
    $("expiry").textContent = `Link expires: ${new Date(manifest.expires_at).toISOString().replace(/Z$/, " UTC")}`;
    $("map-placeholder").hidden = true;
    $("map-tools").hidden = false;
    if (!manifest.layers.length) {
      $("layers").append(textElement("p", "No layers are currently shared.", "layers-empty"));
    }
    const layers = orderedLayers();
    for (const [order, layer] of layers.entries()) {
      const grid = makeGrid(layer, layers.length - order);
      const card = document.createElement("div");
      const record = { layer, grid, card };
      records.push(record);
      card.className = "layer";
      card.dataset.layerId = layer.id;
      const header = document.createElement("div");
      header.className = "layer-header";
      const handle = document.createElement("button");
      handle.type = "button";
      handle.className = "drag-handle";
      handle.hidden = layers.length < 2;
      handle.setAttribute("aria-label", `Reorder ${layer.name}`);
      handle.title = "Drag to reorder, or use the arrow keys";
      handle.append(icon(GRIP_ICON));
      handle.addEventListener("pointerdown", (event) => startDrag(event, record));
      handle.addEventListener("keydown", (event) => {
        const offset = { ArrowUp: -1, ArrowDown: 1 }[event.key];
        if (!offset) return;
        event.preventDefault();
        moveBy(record, offset);
      });
      const label = document.createElement("label");
      label.className = "layer-title";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = previous.get(layer.id)?.visible ?? true;
      grid.setVisible(checkbox.checked);
      checkbox.addEventListener("change", () => {
        grid.setVisible(checkbox.checked);
        card.classList.toggle("layer-hidden", !checkbox.checked);
      });
      card.classList.toggle("layer-hidden", !checkbox.checked);
      label.append(checkbox, textElement("span", layer.name));
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "layer-toggle";
      toggle.append(icon(CHEVRON_ICON));
      const body = document.createElement("div");
      body.className = "layer-body";
      body.id = `layer-body-${layer.id}`;
      toggle.setAttribute("aria-controls", body.id);
      const setCollapsed = (collapsed) => {
        card.classList.toggle("collapsed", collapsed);
        body.hidden = collapsed;
        toggle.setAttribute("aria-expanded", String(!collapsed));
        toggle.setAttribute("aria-label", `${collapsed ? "Show" : "Hide"} ${layer.name} details`);
      };
      setCollapsed(previous.get(layer.id)?.collapsed ?? false);
      toggle.addEventListener("click", () => setCollapsed(!card.classList.contains("collapsed")));
      header.append(handle, label, toggle);
      const opacityLabel = document.createElement("label");
      opacityLabel.className = "opacity";
      const opacity = document.createElement("input");
      opacity.type = "range"; opacity.min = 0; opacity.max = 100;
      opacity.value = Math.round((previous.get(layer.id)?.opacity ?? 1) * 100);
      grid.setOpacity(Number(opacity.value) / 100);
      opacity.setAttribute("aria-label", `${layer.name} opacity`);
      const opacityValue = textElement("span", `${opacity.value}%`, "opacity-value");
      opacityValue.setAttribute("aria-hidden", "true");
      opacity.addEventListener("input", () => {
        grid.setOpacity(Number(opacity.value) / 100);
        opacityValue.textContent = `${opacity.value}%`;
      });
      opacityLabel.append(textElement("span", "Opacity"), opacity, opacityValue);
      body.append(legend(layer), opacityLabel);
      card.append(header, body);
      if (layer.attribution) body.append(textElement("p", `Attribution: ${layer.attribution}`, "layer-detail"));
      if (layer.kind === "vector") {
        const inspect = textElement("button", "Inspect visible features", "inspect");
        inspect.type = "button";
        inspect.addEventListener("click", () => {
          const options = [];
          if (grid.getVisible()) {
            const features = grid.getFeaturesInExtent(map.getView().calculateExtent(map.getSize()));
            options.push(...features.filter((feature) => !maskFeature ||
              booleanIntersects(maskFeature, {
                type: "Feature", properties: {},
                geometry: geojson.writeGeometryObject(feature.getGeometry()),
              })).slice(0, 100));
          }
          const select = document.createElement("select");
          select.setAttribute("aria-label", `${layer.name} visible features (up to 100 tile features)`);
          options.forEach((feature, index) => {
            const option = textElement("option", `Feature ${index + 1}${feature.getId() !== undefined ? ` (ID ${feature.getId()})` : ""}`);
            option.value = index;
            select.append(option);
          });
          select.addEventListener("change", () => selectFeature(options[Number(select.value)], layer.name));
          $("feature-picker")?.remove();
          const picker = document.createElement("div");
          picker.id = "feature-picker";
          picker.append(textElement("p", options.length ? "Visible tile features (up to 100; boundary features may repeat):" : "No features loaded at this zoom."));
          if (options.length) { picker.append(select); selectFeature(options[0], layer.name); }
          body.append(picker);
          if (options.length) select.focus();
        });
        body.append(inspect);
      }
      $("layers").append(card);
    }
    if (fitLayers) fit();
    map.addLayer(basemap);
    map.addLayer(borders);
    for (const record of records) map.addLayer(record.grid);
    status("", "ready");
  }

  async function refresh() {
    if (token) return;
    const result = await request(`${base}/session/refresh`, { method: "POST" });
    if (!Number.isFinite(result.data?.expires_in) || result.data.expires_in <= 0) {
      throw new ViewerError(502, "Invalid session response.");
    }
    sessionDeadline = Date.now() + result.data.expires_in * 1000;
    enforceExpiry();
  }

  async function poll() {
    if (stopped || busy || !manifest) return;
    if (Date.now() >= Date.parse(manifest.expires_at)) {
      stop("This shared map has expired.");
      return;
    }
    busy = true;
    try {
      if (!token && sessionDeadline - Date.now() < 60000) await refresh();
      const updated = validateManifest((await request(`${base}/manifest`)).data, shareId, token);
      if (manifest.mask_version !== undefined && updated.mask_version === undefined) {
        throw new MaskError();
      }
      if (updated.mask_version !== manifest.mask_version) {
        setMask(updated.mask || null);
      }
      const layersChanged = JSON.stringify(updated.layers) !== JSON.stringify(manifest.layers);
      manifest = updated;
      if (layersChanged) render(false);
    } catch (error) {
      if (error instanceof MaskError) stop(error.message);
      else failure(error);
    }
    finally {
      busy = false;
      clearTimeout(timer);
      if (!stopped) timer = setTimeout(poll, 30000);
    }
  }

  async function load() {
    if (busy || stopped) return;
    busy = true;
    $("retry").hidden = true;
    status("Loading shared map...", "loading");
    try {
      await refresh();
      manifest = validateManifest((await request(`${base}/manifest`)).data, shareId, token);
      setMask(manifest.mask || null);
      if (Date.now() >= Date.parse(manifest.expires_at)) { stop("This shared map has expired."); return; }
      enforceExpiry();
      if (stopped) return;
      render();
    } catch (error) {
      if (error instanceof MaskError) stop(error.message);
      else failure(error);
    }
    finally {
      busy = false;
      clearTimeout(timer);
      if (!stopped && manifest) timer = setTimeout(poll, 30000);
    }
  }

  const basemapLabel = document.createElement("label");
  basemapLabel.className = "basemap-control";
  const basemapSelect = document.createElement("select");
  basemapSelect.id = "basemap";
  basemapLabel.append(textElement("span", "Basemap"), basemapSelect);
  const bordersLabel = document.createElement("label");
  bordersLabel.className = "borders-control";
  const bordersCheckbox = document.createElement("input");
  bordersCheckbox.type = "checkbox";
  bordersCheckbox.id = "borders";
  bordersCheckbox.checked = true;
  bordersLabel.append(bordersCheckbox, textElement("span", "National borders"));
  $("basemap-controls").replaceChildren(basemapLabel, bordersLabel);
  for (const [key, option] of Object.entries(BASEMAPS)) {
    const element = textElement("option", option.label);
    element.value = key;
    $("basemap").append(element);
  }
  $("basemap").value = DEFAULT_BASEMAP;
  $("basemap").addEventListener("change", () => {
    const key = $("basemap").value;
    if (!BASEMAPS[key]) return;
    setBasemap(basemap, key);
    borders.set("tone", BASEMAPS[key].borders);
    borders.changed();
  });
  $("borders").addEventListener("change", () => borders.setVisible($("borders").checked));
  $("fit").addEventListener("click", () => {
    fit();
    if (compact.matches) setToc(false);
  });
  $("toc-open").addEventListener("click", () => setToc(true));
  $("toc-close").addEventListener("click", () => setToc(false));
  document.querySelector(".skip-link").addEventListener("click", () => setToc(true, { focus: false }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && compact.matches && tocOpen) setToc(false);
  });
  compact.addEventListener("change", () => { tocOpen = false; syncToc(); });
  syncToc();
  $("disclaimer-toggle").addEventListener("click", () => {
    const expanded = $("map-disclaimer").classList.toggle("expanded");
    $("disclaimer-toggle").setAttribute("aria-expanded", String(expanded));
  });
  $("retry").addEventListener("click", load);
  $("clear-selection").addEventListener("click", () => {
    $("selection").hidden = true;
    $("attributes").replaceChildren();
  });
  map.on("singleclick", async (event) => {
    try {
      if (maskGeometry && !maskGeometry.intersectsCoordinate(event.coordinate)) return;
      for (const record of records) {
        if (record.layer.kind !== "vector" || !record.grid.getVisible()) continue;
        const features = await record.grid.getFeatures(event.pixel);
        if (stopped || !records.includes(record)) return;
        if (features.length) { selectFeature(features[0], record.layer.name); return; }
      }
    } catch (error) { failure(error); }
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { enforceExpiry(); clearTimeout(timer); poll(); }
  });
  window.addEventListener("pagehide", () => {
    clearTimeout(timer);
    for (const controller of pending) controller.abort();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { enforceExpiry(); poll(); }
  });
  if ((!token && !UUID.test(shareId || "")) ||
      (hadFragment && !/^[A-Za-z0-9_-]{32,128}$/.test(initialSecret))) {
    initialSecret = null;
    stop("This share link is invalid. Open the full original link supplied by its owner.");
    return;
  }
  if (initialSecret && !token) {
    try { await request(`${base}/session`, { method: "POST", body: { secret: initialSecret } }); }
    catch (error) {
      initialSecret = null;
      failure(error);
      if (!stopped) stop("Could not authorize this map. Reopen the full original link to retry.");
      return;
    } finally { initialSecret = null; }
  }
  await load();
}

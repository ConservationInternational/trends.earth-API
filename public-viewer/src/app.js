import Map from "ol/Map.js";
import View from "ol/View.js";
import TileLayer from "ol/layer/Tile.js";
import VectorTileLayer from "ol/layer/VectorTile.js";
import XYZ from "ol/source/XYZ.js";
import VectorTileSource from "ol/source/VectorTile.js";
import MVT from "ol/format/MVT.js";
import Feature from "ol/Feature.js";
import TileState from "ol/TileState.js";
import { Circle, Fill, Stroke, Style } from "ol/style.js";
import { fromLonLat, toLonLat, transformExtent } from "ol/proj.js";
import { createEmpty, extend } from "ol/extent.js";
import { defaults as defaultControls } from "ol/control.js";
import { createXYZ } from "ol/tilegrid.js";

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
const $ = (id) => document.getElementById(id);

class ViewerError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function validateManifest(data, shareId) {
  if (!data || data.id !== shareId || typeof data.title !== "string" ||
      !Number.isFinite(Date.parse(data.expires_at)) || !Array.isArray(data.layers) ||
      data.layers.length < 1) {
    throw new ViewerError(502, "The map manifest is invalid.");
  }
  const ids = new Set();
  for (const layer of data.layers) {
    const extension = layer.kind === "vector" ? "pbf" : "png";
    const expected = `/api/v1/public-shares/${shareId}/tiles/${layer.id}/{z}/{x}/{y}.${extension}`;
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
      if (!Object.hasOwn(PALETTES, style.palette) ||
          ![style.min, style.max].every(Number.isFinite) || style.min >= style.max) {
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
  }
  return data;
}

function legend(layer) {
  const container = document.createElement("div");
  const style = layer.style;
  if (style.kind === "continuous") {
    const gradient = document.createElement("div");
    gradient.className = "gradient";
    gradient.style.background = `linear-gradient(to right, ${PALETTES[style.palette].join(",")})`;
    gradient.setAttribute("aria-label", `${style.palette} color ramp from ${style.min} to ${style.max}`);
    const range = document.createElement("div");
    range.className = "legend-range";
    range.append(textElement("span", style.min), textElement("span", style.max));
    container.append(gradient, range, textElement("p", `${style.palette} (representative color ramp)`, "layer-detail"));
    return container;
  }
  const list = document.createElement("ul");
  list.className = "legend";
  const categories = style.kind === "categorical" ? style.values : style.category?.values;
  if (style.category) container.append(textElement("p", `Category: ${style.category.field}`, "layer-detail"));
  const entries = categories ? Object.entries(categories) : [["Features", style.fill]];
  if (style.category) entries.push(["Other / missing", style.fill]);
  for (const [label, color] of entries) {
    const item = document.createElement("li");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.backgroundColor = color;
    swatch.setAttribute("aria-hidden", "true");
    item.append(swatch, textElement("span", `${label} (${color})`));
    list.append(item);
  }
  container.append(list);
  return container;
}

export async function start(initialSecret, hadFragment) {
  const map = new Map({
    target: "map",
    controls: defaultControls({ attribution: false, rotate: false }),
    view: new View({ center: fromLonLat([0, 0]), zoom: 2, minZoom: 0, maxZoom: 22 }),
  });
  $("map").setAttribute("aria-label", "Interactive map. Arrow keys pan; plus and minus zoom.");
  const match = location.pathname.match(/^\/share\/([^/]+)$/);
  const shareId = match?.[1];
  let manifest = null;
  let records = [];
  let stopped = false;
  let busy = false;
  let timer = null;
  let expiryTimer = null;
  let sessionDeadline = 0;
  const pending = new Set();
  const base = `/api/v1/public-shares/${shareId}`;

  function status(message, state = "ready") {
    $("status").textContent = message;
    $("status").dataset.state = state;
    $("status").setAttribute("role", state === "error" ? "alert" : "status");
  }

  function clearLayers() {
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
    const deadline = Math.min(shareExpiry, sessionDeadline);
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
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(path, {
        method, body: body ? JSON.stringify(body) : undefined,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        credentials: "same-origin", cache: "no-store", redirect: "error",
        referrerPolicy: "no-referrer", signal: controller.signal,
      });
      if (!response.ok) throw new ViewerError(response.status, "Map request failed.");
      if (tile && response.status === 204) return null;
      const type = response.headers.get("Content-Type")?.split(";")[0];
      const expected = tile ?
        ["image/png", "application/x-protobuf", "application/vnd.mapbox-vector-tile"] :
        ["application/json"];
      if (!expected.includes(type)) throw new ViewerError(502, "Invalid map response.");
      const limit = tile ? MAX_TILE_BYTES : 1024 * 1024;
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

  function selectFeature(feature, layerName) {
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
    $("selection").scrollIntoView({ block: "nearest" });
  }

  function makeGrid(layer, order) {
    const vector = layer.kind === "vector";
    const extent = transformExtent([
      layer.bounds[0], Math.max(-85.051129, layer.bounds[1]),
      layer.bounds[2], Math.min(85.051129, layer.bounds[3]),
    ], "EPSG:4326", "EPSG:3857");
    let grid;
    async function loadTile(tile, url, tileExtent, projection) {
      try {
        const result = await request(url, { tile: true });
        if (stopped || !map.getLayers().getArray().includes(grid)) return;
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
        failure(error);
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
      source, style: vectorStyle, extent, zIndex: order + 1,
      className: `layer-${layer.id}`, renderMode: "vector",
    }) : new TileLayer({
      source, extent, zIndex: order + 1, className: `layer-${layer.id}`,
    });
    return grid;
  }

  function fit() {
    if (!manifest) return;
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

  function render() {
    clearLayers();
    $("title").textContent = manifest.title;
    // Keep browser tab titles generic: uploaded names may contain sensitive text.
    $("expiry").textContent = `Link expires: ${new Date(manifest.expires_at).toISOString().replace(/Z$/, " UTC")}`;
    $("map-placeholder").hidden = true;
    $("map-tools").hidden = false;
    for (const [order, layer] of manifest.layers.entries()) {
      const grid = makeGrid(layer, order);
      const record = { layer, grid };
      records.push(record);
      const card = document.createElement("div");
      card.className = "layer";
      const label = document.createElement("label");
      label.className = "layer-title";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.addEventListener("change", () => grid.setVisible(checkbox.checked));
      label.append(checkbox, textElement("span", layer.name));
      const opacityLabel = document.createElement("label");
      opacityLabel.className = "opacity";
      const opacity = document.createElement("input");
      opacity.type = "range"; opacity.min = 0; opacity.max = 100; opacity.value = 100;
      opacity.setAttribute("aria-label", `${layer.name} opacity`);
      opacity.addEventListener("input", () => grid.setOpacity(Number(opacity.value) / 100));
      opacityLabel.append(textElement("span", "Opacity"), opacity);
      card.append(label, textElement("p",
        layer.kind === "vector" ? `Vector tiles, zooms ${layer.vector_metadata.minzoom}-${layer.vector_metadata.maxzoom}` :
          `Raster band ${layer.band}`, "layer-detail"), opacityLabel, legend(layer));
      if (layer.attribution) card.append(textElement("p", `Attribution: ${layer.attribution}`, "layer-detail"));
      if (layer.kind === "vector") {
        const inspect = textElement("button", "Inspect visible features");
        inspect.type = "button";
        inspect.addEventListener("click", () => {
          const options = [];
          if (grid.getVisible()) options.push(...grid.getFeaturesInExtent(
            map.getView().calculateExtent(map.getSize())).slice(0, 100));
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
          card.append(picker);
          if (options.length) select.focus();
        });
        card.append(inspect);
      }
      $("layers").append(card);
    }
    fit();
    for (const record of records) map.addLayer(record.grid);
    status("Map ready. Select layers or inspect vector features.");
  }

  async function refresh() {
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
      if (sessionDeadline - Date.now() < 60000) await refresh();
      await request(`${base}/manifest`);
    } catch (error) { failure(error); }
    finally {
      busy = false;
      if (!stopped) timer = setTimeout(poll, 30000);
    }
  }

  async function load() {
    if (busy || stopped) return;
    busy = true;
    $("retry").hidden = true;
    status("Loading shared map...");
    try {
      await refresh();
      manifest = validateManifest((await request(`${base}/manifest`)).data, shareId);
      if (Date.now() >= Date.parse(manifest.expires_at)) { stop("This shared map has expired."); return; }
      enforceExpiry();
      if (stopped) return;
      render();
    } catch (error) { failure(error); }
    finally {
      busy = false;
      clearTimeout(timer);
      if (!stopped && manifest) timer = setTimeout(poll, 30000);
    }
  }

  $("fit").addEventListener("click", fit);
  $("retry").addEventListener("click", load);
  $("clear-selection").addEventListener("click", () => {
    $("selection").hidden = true;
    $("attributes").replaceChildren();
  });
  map.on("moveend", () => {
    const center = toLonLat(map.getView().getCenter());
    $("coordinates").textContent = `Center: ${center[1].toFixed(3)}, ${center[0].toFixed(3)}; zoom ${map.getView().getZoom().toFixed(1)}`;
  });
  map.on("singleclick", async (event) => {
    try {
      for (const record of [...records].reverse()) {
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
  if (!UUID.test(shareId || "") || (hadFragment && !/^[A-Za-z0-9_-]{32,128}$/.test(initialSecret))) {
    initialSecret = null;
    stop("This share link is invalid. Open the full original link supplied by its owner.");
    return;
  }
  if (initialSecret) {
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

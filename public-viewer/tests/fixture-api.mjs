import http from "node:http";
import { deflateSync, gzipSync } from "node:zlib";

const shares = new Map();
const requests = [];

function varint(value) {
  const bytes = [];
  do {
    bytes.push((value & 127) | (value > 127 ? 128 : 0));
    value = Math.floor(value / 128);
  } while (value);
  return Buffer.from(bytes);
}
function field(number, data) {
  return Buffer.concat([varint(number * 8 + 2), varint(data.length), data]);
}
function geometry(points, close = false, cursor = [0, 0]) {
  const zigzag = (n) => n >= 0 ? n * 2 : -n * 2 - 1;
  const output = [9, zigzag(points[0][0] - cursor[0]), zigzag(points[0][1] - cursor[1])];
  if (points.length > 1) output.push(((points.length - 1) << 3) | 2);
  for (let i = 1; i < points.length; i++) {
    for (let j = 0; j < 2; j++) {
      const delta = points[i][j] - points[i - 1][j];
      output.push(delta >= 0 ? delta * 2 : -delta * 2 - 1);
    }
  }
  if (close) output.push(15);
  return Buffer.concat(output.map(varint));
}
function mvt() {
  const features = [
    [1, [[768, 2048]], false],
    [2, [[256, 768], [3840, 768]], false],
    [3, [[1024, 1024], [3072, 1024], [3072, 3072], [1024, 3072]], true],
  ].map(([type, points, close], index) => {
    const feature = Buffer.concat([
      Buffer.from([8, index + 1]), field(2, Buffer.from([0, index])),
      Buffer.from([24, type]), field(4, Buffer.concat([
        geometry(points, close),
        ...(type === 3 ? [geometry([[1792, 1792], [1792, 2304], [2304, 2304], [2304, 1792]],
          true, points.at(-1))] : []),
      ])),
    ]);
    return field(2, feature);
  });
  const layer = Buffer.concat([
    field(1, Buffer.from("analysis")), ...features,
    field(3, Buffer.from("status")),
    ...["point", "line", "polygon"].map((value) => field(4, field(1, Buffer.from(value)))),
    Buffer.from([40, 128, 32, 120, 2]),
  ]);
  return field(3, layer);
}

// A solid green PNG; its known pixels make actual raster rendering testable.
function png() {
  const crc32 = (data) => {
    let crc = 0xffffffff;
    for (const byte of data) {
      crc ^= byte;
      for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    return (crc ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const payload = Buffer.concat([Buffer.from(type), data]);
    const size = Buffer.alloc(4); size.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(payload));
    return Buffer.concat([size, payload, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(256, 0); header.writeUInt32BE(256, 4);
  header[8] = 8; header[9] = 6;
  const pixels = Buffer.alloc(256 * (1 + 256 * 4));
  for (let y = 0; y < 256; y++) for (let x = 0; x < 256; x++) {
    const offset = y * 1025 + 1 + x * 4;
    pixels[offset + 1] = 128; pixels[offset + 3] = 255;
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(pixels)), chunk("IEND", Buffer.alloc(0)),
  ]);
}
const raster = png();
const vector = mvt();
const rasterId = "11111111-1111-4111-8111-111111111111";
const vectorId = "22222222-2222-4222-8222-222222222222";

// Like the real API, the manifest echoes what the plugin uploaded (title, layer
// names, attribution and styles including legend labels) and only adds
// server-generated fields. This fake API defines no display text of its own.
function manifest(id, options, token) {
  const base = token ? `/api/v1/public-shares/tiles/${id}/${token}/` :
    `/api/v1/public-shares/${id}/tiles/`;
  const [raster, vector] = options.layers;
  return {
    id, title: options.title,
    expires_at: options.expires_at || new Date(Date.now() + 86400000).toISOString(),
    layers: [
      {
        ...raster, id: rasterId, kind: "raster", band: 1,
        bounds: [-180, -85, 180, 85], crs: "EPSG:4326", nodata: null,
        vector_metadata: null, tile_format: "png", tiles: base + rasterId + "/{z}/{x}/{y}.png",
      },
      {
        ...vector, id: vectorId, kind: "vector", band: null,
        bounds: [-180, -85, 180, 85], crs: "EPSG:3857", nodata: null,
        vector_metadata: { source_layer: "analysis", minzoom: 0, maxzoom: 4, fields: { status: "string" } },
        tile_format: "pbf", tiles: base + vectorId + "/{z}/{x}/{y}.pbf",
      },
    ],
  };
}

http.createServer(async (req, res) => {
  const path = req.url.split("?")[0];
  let body = "";
  for await (const data of req) body += data;
  const json = (status, data) => {
    res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify(data));
  };
  if (path === "/health") { res.end("OK"); return; }
  if (path === "/__requests") { json(200, requests); return; }
  if (path.startsWith("/__share/")) {
    const id = path.split("/")[2];
    const options = JSON.parse(body || "{}");
    shares.set(id, { ...shares.get(id), ...options });
    json(200, { id }); return;
  }
  requests.push({ path, method: req.method, origin: req.headers.origin, cookie: req.headers.cookie, body });
  const tokenMatch = path.match(/^\/api\/v1\/public-shares\/s\/([^/]+)\/(.+)$/);
  const tileMatch = path.match(/^\/api\/v1\/public-shares\/tiles\/([^/]+)\/([^/]+)\/(.+)$/);
  const token = tokenMatch?.[1] || tileMatch?.[2];
  const tokenId = token && [...shares.keys()].find((id) => id.replaceAll("-", "") === token);
  const match = tokenMatch ? [path, tokenId, tokenMatch[2]] : tileMatch ?
    [path, tokenId === tileMatch[1] ? tokenId : null, `tiles/${tileMatch[3]}`] :
    path.match(/^\/api\/v1\/public-shares\/([^/]+)\/(.+)$/);
  if (!match || !shares.has(match[1])) { json(404, {}); return; }
  const [, id, action] = match;
  const options = shares.get(id);
  if (action === "session") {
    if (options.unavailable || JSON.parse(body || "{}").secret !== "a".repeat(43)) {
      json(404, {}); return;
    }
    if (req.headers.origin !== `https://${req.headers.host}`) { json(403, {}); return; }
    res.setHeader("Set-Cookie",
      `te_share_session=${id}; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/public-shares/${id}`);
    json(200, { data: { expires_in: 900 } }); return;
  }
  if (options.unavailable || (!token && !req.headers.cookie?.includes(`te_share_session=${id}`))) {
    json(404, {}); return;
  }
  if (action === "session/refresh") {
    if (req.headers.origin !== `https://${req.headers.host}`) { json(403, {}); return; }
    if (options.sessionExpired) { json(404, {}); return; }
    json(200, { data: { expires_in: options.sessionTtl || 900 } }); return;
  }
  if (action === "manifest") {
    if (options.manifestFailure) { json(503, {}); return; }
    const data = manifest(id, options, token);
    if (options.maliciousUrl) data.layers[0].tiles = "https://external.invalid/{z}/{x}/{y}.png";
    json(200, { data }); return;
  }
  if (action.startsWith("tiles/")) {
    if (options.tileFailure) { json(options.tileFailure, {}); return; }
    const isVector = action.endsWith(".pbf");
    if (isVector && options.emptyVector) { res.writeHead(204); res.end(); return; }
    res.writeHead(200, {
      "Content-Type": isVector ? "application/vnd.mapbox-vector-tile" : "image/png",
      "Cache-Control": "no-store",
      ...(isVector && options.gzip ? { "Content-Encoding": "gzip" } : {}),
    });
    res.end(isVector ? (options.gzip ? gzipSync(vector) : vector) : raster); return;
  }
  json(404, {});
}).listen(3000, "0.0.0.0");

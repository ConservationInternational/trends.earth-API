import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

const secret = "a".repeat(43);
const pathToken = (id) => id.replaceAll("-", "");
const fixtureUrl = `http://127.0.0.1:${process.env.FIXTURE_PORT || 18952}`;
const vectorCanvas = ".layer-22222222-2222-4222-8222-222222222222 canvas";
const rasterCanvas = ".layer-11111111-1111-4111-8111-111111111111 canvas";

async function colorCount(page, selector, color) {
  return page.locator(selector).first().evaluate((canvas, expected) => {
    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (expected.every((value, j) => pixels[i + j] === value)) count++;
    }
    return count;
  }, color);
}
const transparentPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");
let mapboxRequests = [];

test.beforeEach(async ({ page }) => {
  mapboxRequests = [];
  page.on("pageerror", (error) => console.error(error.message));
  page.on("console", (msg) => { if (msg.type() === "error") console.error(msg.text()); });
  await page.route("https://api.mapbox.com/**", async (route) => {
    const request = route.request();
    mapboxRequests.push({ url: request.url(), referer: await request.headerValue("referer") });
    const vector = request.url().includes(".mvt");
    await route.fulfill({
      status: 200,
      headers: { "Access-Control-Allow-Origin": "*" },
      contentType: vector ? "application/vnd.mapbox-vector-tile" : "image/png",
      body: vector ? Buffer.alloc(0) : transparentPng,
    });
  });
});

// Test inputs standing in for the plugin's upload declarations. In production
// the plugin builds these names and (translated) legend labels with the same code
// it uses for the QGIS layers panel; the fixture API only echoes them back.
const uploadedTitle = "Uploaded map title";
function upload(options) {
  const rasterStyle = options.customRamp ?
    { kind: "continuous", min: -1, max: 1, stops: [
      { value: -1, color: "#0000ff", label: "Uploaded stop A" }, { value: 0, color: "#ffffff" },
      { value: 1, color: "#ff0000", label: "Uploaded stop B" },
    ] } :
    options.continuous ?
      { kind: "continuous", min: 0, max: 100, palette: "viridis" } :
      { kind: "categorical", values: { "2": "#ffffff", "-1": "#ff00ff", "1": "#008000" },
        labels: { "-1": "Uploaded class -1", "1": "Uploaded class 1" } };
  const category = {
    field: "status", values: { point: "#ff0000", line: "#0000ff", polygon: "#ffff00" },
    labels: { polygon: "Uploaded polygon label" }, other_label: "Uploaded other label",
  };
  if (options.missingCategory) delete category.values.point;
  return {
    title: options.title ?? uploadedTitle,
    layers: [
      { name: "Raster result", attribution: options.attribution ?? "Raster attribution", style: rasterStyle },
      {
        name: "Vector result", attribution: "Vector attribution",
        style: { kind: "vector", fill: "#cccccc", stroke: "#333333", width: 3, fill_opacity: 1, radius: 8, category },
      },
    ],
  };
}

async function share(request, options = {}) {
  const id = randomUUID();
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { ...options, ...upload(options) } });
  return id;
}
const ready = (page) => expect(page.locator("#status")).toHaveAttribute("data-state", "ready");
async function open(page, id, legacy = false) {
  await page.goto(legacy ? `/share/${id}#${secret}` : `/share/s/${pathToken(id)}`);
  await ready(page);
  await expect(page.locator(".brand-logo")).toBeVisible();
  await expect(page.locator(".powered-by")).toHaveText("Powered by Trends.Earth");
  await expect(page.locator("header")).toHaveCSS("background-color", "rgb(100, 100, 100)");
  await expect.poll(() => page.locator(".brand-logo").evaluate((image) =>
    image.complete && image.naturalWidth > 0)).toBe(true);
  await expect(page.locator(vectorCanvas).first()).toBeVisible();
}

test("clips raster and vector layers and applies mask changes to an open map", async ({ page, request }) => {
  await page.clock.install();
  const mask = {
    type: "Polygon",
    coordinates: [[[-180, -80], [0, -80], [0, 80], [-180, 80], [-180, -80]]],
  };
  const id = await share(request, { mask, mask_version: 0 });
  await open(page, id);
  const pixels = (selector) => page.locator(selector).first().evaluate((canvas) => {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let left = 0, right = 0;
    for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
      if (data[(y * canvas.width + x) * 4 + 3] === 255) {
        if (x < canvas.width / 2) left++; else right++;
      }
    }
    return { left, right };
  });
  await expect.poll(async () => (await pixels(rasterCanvas)).left).toBeGreaterThan(1000);
  await expect.poll(async () => (await pixels(rasterCanvas)).right).toBeLessThan(1000);
  await expect.poll(async () => (await pixels(vectorCanvas)).left).toBeGreaterThan(100);
  await expect.poll(async () => (await pixels(vectorCanvas)).right).toBeLessThan(100);
  await page.getByRole("checkbox", { name: "Vector result" }).uncheck();
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { mask: null, mask_version: 1 } });
  await page.clock.runFor(30001);
  await expect.poll(async () => (await pixels(rasterCanvas)).right).toBeGreaterThan(1000);
  await expect(page.getByRole("checkbox", { name: "Vector result" })).not.toBeChecked();
});

test("masks holes and excludes outside features from inspection", async ({ page, request }) => {
  const mask = {
    type: "Polygon",
    coordinates: [
      [[-80, -60], [80, -60], [80, 60], [-80, 60], [-80, -60]],
      [[-20, -20], [-20, 20], [20, 20], [20, -20], [-20, -20]],
    ],
  };
  const id = await share(request, { mask });
  await open(page, id);
  const centerAlpha = () => page.locator(rasterCanvas).first().evaluate((canvas) =>
    canvas.getContext("2d").getImageData(
      Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data[3]);
  await expect.poll(centerAlpha).toBe(0);
  await expect.poll(() => colorCount(page, rasterCanvas, [0, 128, 0, 255])).toBeGreaterThan(1000);
  await page.locator("#map").click({ position: { x: 5, y: 5 } });
  await expect(page.locator("#selection")).toBeHidden();
});

test("the feature picker ignores features outside a mask", async ({ page, request }) => {
  const mask = {
    type: "Polygon",
    coordinates: [[[-179, -84], [-175, -84], [-175, -80], [-179, -80], [-179, -84]]],
  };
  const id = await share(request, { mask });
  await open(page, id);
  await page.getByRole("button", { name: "Inspect visible features" }).click();
  await expect(page.locator("#feature-picker")).toContainText("No features loaded");
  await expect(page.locator("#selection")).toBeHidden();
});

test("invalid masks fail closed", async ({ page, request }) => {
  const id = await share(request, {
    mask: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1]]] },
  });
  await page.goto(`/share/s/${pathToken(id)}`);
  await expect(page.locator("#status")).toContainText("invalid mask");
  await expect(page.locator("#layers")).toBeEmpty();
});

test.describe("high-DPI masking", () => {
  test.use({ deviceScaleFactor: 2 });
  test("clips in canvas pixels", async ({ page, request }) => {
    const id = await share(request, {
      mask: {
        type: "Polygon",
        coordinates: [[[-180, -80], [0, -80], [0, 80], [-180, 80], [-180, -80]]],
      },
    });
    await open(page, id);
    const samples = () => page.locator(rasterCanvas).first().evaluate((canvas) => {
      const context = canvas.getContext("2d");
      const y = Math.floor(canvas.height / 2);
      return [0.25, 0.75].map((x) =>
        context.getImageData(Math.floor(canvas.width * x), y, 1, 1).data[3]);
    });
    await expect.poll(samples).toEqual([255, 0]);
  });
});

test("renders authorized raster/vector tiles, legends, controls and safe attributes", async ({ page, request }) => {
  const id = await share(request);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await open(page, id);
  await expect(page.locator("#title")).toHaveText(uploadedTitle);
  await expect(page.locator("#expiry")).toContainText("UTC");
  await expect(page.locator("#layers")).toContainText("Uploaded polygon label");
  await expect(page.locator("#layers")).toContainText("Uploaded other label");
  await expect(page.locator("#layers")).not.toContainText(/Category:|Other \/ missing/);
  await expect(page.locator("#layers")).toContainText("point");
  await expect(page.locator("#layers .legend").first().locator("li")).toHaveText(
    ["Uploaded class -1", "Uploaded class 1", "2"]);
  await expect(page.locator("#map-tools")).not.toContainText(/arrow keys|Center:/);
  await expect(page.locator("#status")).toBeHidden();
  await expect(page.locator("body")).not.toContainText(/No analytics|Map ready/);
  await expect(page.locator("#layers")).not.toContainText(/#[0-9a-f]{6}|Raster band|Vector tiles/i);
  for (const color of [[255, 255, 0, 255], [255, 0, 0, 255], [0, 0, 255, 255]]) {
    await expect.poll(() => colorCount(page, vectorCanvas, color)).toBeGreaterThan(50);
  }
  await expect.poll(() => colorCount(page, rasterCanvas, [0, 128, 0, 255])).toBeGreaterThan(10000);
  const holeVisible = await page.locator(vectorCanvas).evaluate((canvas) => {
    const { width, height } = canvas;
    const pixels = canvas.getContext("2d").getImageData(0, 0, width, height).data;
    const yellow = (x, y) => {
      const offset = (y * width + x) * 4;
      return pixels[offset] === 255 && pixels[offset + 1] === 255 &&
        pixels[offset + 2] === 0 && pixels[offset + 3] === 255;
    };
    for (let y = 0; y < height; y++) {
      for (let x = 8; x < width - 72; x++) {
        if (!yellow(x, y) || !yellow(x - 8, y)) continue;
        let end = x;
        while (end < width && yellow(end, y)) end++;
        let transparent = false;
        for (let next = end; next < Math.min(end + 64, width - 8); next++) {
          if (pixels[(y * width + next) * 4 + 3] === 0) transparent = true;
          if (transparent && yellow(next, y) && yellow(next + 8, y)) return true;
        }
      }
    }
    return false;
  });
  expect(holeVisible).toBe(true);
  const point = await page.locator(vectorCanvas).evaluate((canvas) => {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const rect = canvas.getBoundingClientRect();
    const mapRect = document.getElementById("map").getBoundingClientRect();
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] === 255 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 255) {
        const x = rect.left + (i / 4 % canvas.width) * rect.width / canvas.width;
        const y = rect.top + Math.floor(i / 4 / canvas.width) * rect.height / canvas.height;
        if (x > mapRect.left + 50 && x < mapRect.right - 50 &&
            y > mapRect.top + 50 && y < mapRect.bottom - 50) return { x, y };
      }
    }
  });
  await page.mouse.click(point.x + 3, point.y + 8);
  await expect(page.locator("#attributes")).toContainText("point");
  await page.getByRole("button", { name: "Inspect visible features" }).click();
  await expect(page.locator("#attributes")).toContainText("status");
  await expect(page.locator("#attributes")).toContainText("point");
  const lineOption = await page.getByRole("combobox", { name: /Vector result visible features/ }).locator("option").filter({ hasText: "(ID 2)" }).first().getAttribute("value");
  await page.getByRole("combobox", { name: /Vector result visible features/ }).selectOption(lineOption);
  await expect(page.locator("#attributes")).toContainText("line");
  await page.getByRole("slider", { name: "Vector result opacity" }).fill("40");
  await expect.poll(() => colorCount(page, vectorCanvas, [255, 255, 0, 102])).toBeGreaterThan(50);
  await page.getByRole("checkbox", { name: "Vector result" }).uncheck();
  await expect(page.locator(vectorCanvas).first()).toBeHidden();
  await page.getByRole("checkbox", { name: "Vector result" }).check();
  await expect(page.locator(vectorCanvas).first()).toBeVisible();
  await page.getByRole("button", { name: "Show full extent" }).click();
  expect(errors).toEqual([]);
});

test("renders earlier layers above later layers in the table of contents", async ({ page, request }) => {
  for (const reverseLayers of [false, true]) {
    const id = await share(request, { reverseLayers });
    await open(page, id);
    await expect(page.locator(rasterCanvas).first()).toBeVisible();
    const names = reverseLayers ? ["Vector result", "Raster result"] : ["Raster result", "Vector result"];
    await expect(page.locator("#layers .layer-title span")).toHaveText(names);
    await expect.poll(() => page.evaluate(() => {
      const raster = document.querySelector(".layer-11111111-1111-4111-8111-111111111111");
      const vector = document.querySelector(".layer-22222222-2222-4222-8222-222222222222");
      return Boolean(raster.compareDocumentPosition(vector) & Node.DOCUMENT_POSITION_FOLLOWING);
    })).toBe(reverseLayers);
  }
});

test("viewers can drag or key layers into a new local order", async ({ page, request }) => {
  await page.clock.install();
  const id = await share(request);
  await open(page, id);
  await expect(page.locator("#layers-title")).toHaveText("Layers");
  const titles = page.locator("#layers .layer-title span");
  const vectorOnTop = () => page.evaluate(() => {
    const raster = document.querySelector(".layer-11111111-1111-4111-8111-111111111111");
    const vector = document.querySelector(".layer-22222222-2222-4222-8222-222222222222");
    return Boolean(raster.compareDocumentPosition(vector) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  await expect(titles).toHaveText(["Raster result", "Vector result"]);
  await expect.poll(vectorOnTop).toBe(false);

  const handle = await page.getByRole("button", { name: "Reorder Vector result" }).boundingBox();
  const target = await page.locator("#layers .layer").first().boundingBox();
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2, target.y + 2, { steps: 8 });
  await page.mouse.up();
  await expect(titles).toHaveText(["Vector result", "Raster result"]);
  await expect.poll(vectorOnTop).toBe(true);

  await page.getByRole("button", { name: "Reorder Vector result" }).press("ArrowDown");
  await expect(titles).toHaveText(["Raster result", "Vector result"]);
  await expect.poll(vectorOnTop).toBe(false);
  await expect(page.getByRole("button", { name: "Reorder Vector result" })).toBeFocused();
  await page.getByRole("button", { name: "Reorder Vector result" }).press("ArrowUp");
  await expect(titles).toHaveText(["Vector result", "Raster result"]);

  await page.getByRole("button", { name: "Reorder Vector result" }).press("ArrowDown");
  await page.getByRole("button", { name: "Hide Raster result details" }).click();
  await expect(page.getByRole("slider", { name: "Raster result opacity" })).toBeHidden();
  // The owner's new order rerenders the list but keeps the viewer's order and layout.
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { reverseLayers: true } });
  await page.clock.runFor(30001);
  await expect(page.getByRole("button", { name: "Show Raster result details" })).toBeVisible();
  await expect(titles).toHaveText(["Raster result", "Vector result"]);
  await expect.poll(vectorOnTop).toBe(false);
});

test("inspects the uppermost visible vector layer first", async ({ page, request }) => {
  const id = await share(request, { secondVector: "Another vector" });
  await open(page, id);
  await expect.poll(() => colorCount(page, vectorCanvas, [255, 0, 0, 255])).toBeGreaterThan(50);
  const point = await page.locator(vectorCanvas).first().evaluate((canvas) => {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const rect = canvas.getBoundingClientRect();
    const mapRect = document.getElementById("map").getBoundingClientRect();
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] === 255 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 255) {
        const x = rect.left + (i / 4 % canvas.width) * rect.width / canvas.width;
        const y = rect.top + Math.floor(i / 4 / canvas.width) * rect.height / canvas.height;
        if (x > mapRect.left + 50 && x < mapRect.right - 50 &&
            y > mapRect.top + 50 && y < mapRect.bottom - 50) return { x, y };
      }
    }
  });
  await page.mouse.click(point.x + 3, point.y + 8);
  await expect(page.locator("#attributes dd").first()).toHaveText("Vector result");
});

test("keeps an empty map accessible and updates an open viewer when layers change", async ({ page, request }) => {
  await page.clock.install();
  const id = await share(request, { reverseLayers: true });
  await open(page, id);
  await page.getByRole("slider", { name: "Vector result opacity" }).fill("40");
  await page.getByRole("checkbox", { name: "Vector result" }).uncheck();
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { layerCount: 1 } });
  await page.clock.runFor(30001);
  await expect(page.locator("#layers .layer-title span")).toHaveText(["Vector result"]);
  await expect(page.getByRole("checkbox", { name: "Vector result" })).not.toBeChecked();
  await expect(page.getByRole("slider", { name: "Vector result opacity" })).toHaveValue("40");
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { layerCount: 0 } });
  await page.clock.runFor(30001);
  await expect(page.locator("#layers")).toContainText("No layers are currently shared.");
  await expect(page.locator("#layers .layer")).toHaveCount(0);
  await ready(page);
  await page.getByRole("button", { name: "Show full extent" }).click();
  await page.reload();
  await ready(page);
  await expect(page.locator("#layers")).toContainText("No layers are currently shared.");

  await request.post(`${fixtureUrl}/__share/${id}`, { data: { layerCount: 1 } });
  await page.clock.runFor(30001);
  await expect(page.locator("#layers .layer-title span")).toHaveText(["Vector result"]);
});

test("clears data at the declared expiry without waiting for polling", async ({ page, request }) => {
  await page.clock.install();
  const expires = Date.now() + 10000;
  const id = await share(request, { expires_at: new Date(expires).toISOString() });
  await open(page, id);
  await page.clock.runFor(10001);
  await expect(page.locator("#status")).toContainText("expired");
  await expect(page.locator("#layers")).toBeEmpty();
  await expect(page.locator("#map canvas")).toHaveCount(0);
});

test("renders gzip vector tiles, missing categories, and empty tiles", async ({ page, request }) => {
  const id = await share(request, { gzip: true, missingCategory: true });
  await open(page, id);
  await expect.poll(() => colorCount(page, vectorCanvas, [204, 204, 204, 255])).toBeGreaterThan(50);
  const empty = await share(request, { emptyVector: true });
  await open(page, empty);
  await ready(page);
  await expect.poll(() => colorCount(page, vectorCanvas, [255, 255, 0, 255])).toBe(0);
});

test("removes fragment before requests and does not leak capabilities or send external requests", async ({ page, request, context }) => {
  const id = await share(request, { title: "<img src=x onerror=alert(1)>", attribution: "<script>alert(2)</script>" });
  const requests = [];
  page.on("request", (req) => requests.push(req));
  await open(page, id, true);
  expect(new URL(page.url()).hash).toBe("");
  await expect(page.locator("#title")).toHaveText("<img src=x onerror=alert(1)>");
  await expect(page.locator("#title img")).toHaveCount(0);
  const exchange = requests.find((req) => req.url().endsWith("/session"));
  expect(JSON.parse(exchange.postData()).secret).toBe(secret);
  for (const req of requests) {
    // Only the share API and the Resilience Atlas Mapbox basemap/borders are contacted.
    expect([new URL(page.url()).origin, "https://api.mapbox.com"]).toContain(new URL(req.url()).origin);
    expect(req.url()).not.toContain(secret);
    expect(req.headers().referer || "").toBe("");
    if (req !== exchange) expect(req.postData() || "").not.toContain(secret);
  }
  const cookie = (await context.cookies()).find((cookie) => cookie.name === "te_share_session");
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.secure).toBe(true);
  expect(cookie.path).toBe(`/api/v1/public-shares/${id}`);
  await page.reload();
  await ready(page);
});

test("gateway blocks dashboard, owner APIs, callbacks and non-public renderer endpoints", async ({ request }) => {
  const id = await share(request);
  const html = await request.get(`/share/${id}`);
  expect(html.headers()["cache-control"]).toBe("no-store");
  expect(html.headers()["referrer-policy"]).toBe("no-referrer");
  const csp = html.headers()["content-security-policy"];
  expect(csp).toContain("sha256-");
  expect(csp).not.toContain("script-src 'self' 'unsafe-inline'");
  expect(csp).not.toContain("unsafe-eval");
  for (const path of ["/api/v1/shares", "/api/v1/users", "/catalog", "/_dash-update-component", "/dashboard"]) {
    expect((await request.get(path)).status()).toBe(404);
  }
  expect((await request.post("/_dash-update-component", { data: {} })).status()).toBe(404);
  expect((await request.get(`/api/v1/public-shares/${id}/manifest`)).status()).toBe(404);
});

test("path links survive reload and forwarding without cookies or a secret exchange", async ({ page, request, context, browser }) => {
  const id = await share(request);
  const calls = [];
  page.on("request", (req) => calls.push(req));
  await open(page, id);
  const link = page.url();
  expect(new URL(link).pathname).toBe(`/share/s/${pathToken(id)}`);
  expect((await context.cookies()).filter((cookie) => cookie.name === "te_share_session")).toEqual([]);
  expect(calls.some((req) => req.url().includes("/session"))).toBe(false);
  expect(mapboxRequests.every(({ referer, url }) => !referer && !url.includes(pathToken(id)))).toBe(true);
  await page.reload();
  await ready(page);
  const recipient = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const forwarded = await recipient.newPage();
    await forwarded.route("https://api.mapbox.com/**", (route) => route.abort());
    await forwarded.goto(link);
    await ready(forwarded);
    await expect.poll(() => colorCount(forwarded, rasterCanvas, [0, 128, 0, 255])).toBeGreaterThan(10000);
  } finally { await recipient.close(); }
});

test("limits tiles to six concurrent requests and recovers from transient failures before warning", async ({ page, request }) => {
  const id = await share(request);
  let active = 0;
  let maximum = 0;
  let retriedUrl = null;
  let attempts = 0;
  await page.route("**/api/v1/public-shares/tiles/**", async (route) => {
    const url = route.request().url();
    if (!retriedUrl && url.endsWith(".png")) retriedUrl = url;
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, 100));
    try {
      if (url === retriedUrl && ++attempts < 3) {
        await route.fulfill({ status: 503, headers: { "Retry-After": "1" } });
      } else {
        await route.continue();
      }
    } finally { active--; }
  });
  await open(page, id);
  await expect.poll(() => attempts).toBe(3);
  await expect.poll(() => colorCount(page, rasterCanvas, [0, 128, 0, 255])).toBeGreaterThan(10000);
  expect(maximum).toBeLessThanOrEqual(6);
  await expect(page.locator("#status")).toHaveAttribute("data-state", "ready");
  await expect(page.getByRole("button", { name: "Retry loading map" })).toBeHidden();
});

test("waits for slow tiles beyond the former 15-second deadline", async ({ page, request }) => {
  await page.clock.install();
  const id = await share(request);
  const held = [];
  await page.route("**/api/v1/public-shares/tiles/**", (route) => held.push(route));
  await page.goto(`/share/s/${pathToken(id)}`);
  await ready(page);
  await expect.poll(() => held.length).toBeGreaterThan(0);
  await page.clock.runFor(16000);
  await page.unroute("**/api/v1/public-shares/tiles/**");
  for (const route of held) await route.continue();
  await page.clock.resume();
  await expect.poll(() => colorCount(page, rasterCanvas, [0, 128, 0, 255])).toBeGreaterThan(10000);
  await expect(page.locator("#status")).toHaveAttribute("data-state", "ready");
});

test("warns only after tile retries are exhausted and never retries authorization failures", async ({ page, request }) => {
  const id = await share(request, { tileFailure: 504 });
  await page.goto(`/share/s/${pathToken(id)}`);
  await expect(page.locator("#status")).toContainText("temporarily unavailable", { timeout: 10000 });
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { tileFailure: 0 } });
  await page.getByRole("button", { name: "Retry loading map" }).click();
  await ready(page);
  await expect.poll(() => colorCount(page, rasterCanvas, [0, 128, 0, 255])).toBeGreaterThan(10000);
  const denied = await share(request, { tileFailure: 403 });
  await page.goto(`/share/s/${pathToken(denied)}`);
  await expect(page.locator("#status")).toContainText("unavailable");
  await expect(page.locator("#layers")).toBeEmpty();
});

test("invalid and unavailable links fail without displaying map data", async ({ page, request }) => {
  const id = await share(request, { unavailable: true });
  await page.goto(`/share/${id}#${secret}`);
  await expect(page.locator("#status")).toContainText("unavailable");
  await expect(page.locator("#map canvas")).toHaveCount(0);
  await page.goto(`/share/${id}#invalid`);
  await expect(page.locator("#status")).toContainText("invalid");
});

test("rejects external tile URLs rather than trusting the manifest", async ({ page, request }) => {
  const id = await share(request, { maliciousUrl: true });
  await page.goto(`/share/${id}#${secret}`);
  await expect(page.locator("#status")).toContainText("temporarily unavailable");
  await expect(page.locator("#map canvas")).toHaveCount(0);
});

test("reports service errors and retries successfully", async ({ page, request }) => {
  const id = await share(request, { manifestFailure: true });
  await page.goto(`/share/${id}#${secret}`);
  await expect(page.getByRole("button", { name: "Retry loading map" })).toBeVisible();
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { manifestFailure: false } });
  await page.getByRole("button", { name: "Retry loading map" }).click();
  await ready(page);
});

test("clears displayed data when revoked and refresh cannot extend an expired session", async ({ page, request }) => {
  await page.clock.install();
  const id = await share(request, { sessionTtl: 60 });
  await open(page, id, true);
  await request.post(`${fixtureUrl}/__share/${id}`, { data: { sessionExpired: true } });
  await page.clock.fastForward(31000);
  await expect(page.locator("#status")).toContainText("unavailable");
  await expect(page.locator("#map canvas")).toHaveCount(0);
  await expect(page.locator("#layers")).toBeEmpty();
  const other = await share(request);
  await open(page, other);
  await request.post(`${fixtureUrl}/__share/${other}`, { data: { unavailable: true } });
  await page.clock.fastForward(31000);
  await expect(page.locator("#status")).toContainText("unavailable");
});

test("shows expired state and supports responsive keyboard navigation and continuous legends", async ({ page, request }) => {
  const expired = await share(request, { expires_at: new Date(Date.now() - 1000).toISOString() });
  await page.goto(`/share/${expired}#${secret}`);
  await expect(page.locator("#status")).toContainText("expired");
  await page.setViewportSize({ width: 390, height: 844 });
  const id = await share(request, { continuous: true });
  await open(page, id);
  await page.getByRole("button", { name: "Layers", exact: true }).click();
  await expect(page.locator(".gradient")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("checkbox", { name: "Raster result" }).focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("checkbox", { name: "Raster result" })).not.toBeChecked();
});

test("renders a custom color-stop legend", async ({ page, request }) => {
  const id = await share(request, { customRamp: true });
  await open(page, id);
  const gradient = page.locator(".gradient").first();
  await expect(gradient).toHaveAttribute(
    "aria-label",
    "Color ramp from -1 to 1",
  );
  await expect(gradient).toHaveCSS(
    "background-image",
    /rgb\(0, 0, 255\).*rgb\(255, 0, 0\)/,
  );
  await expect(page.locator("#layers .legend").first().locator("li")).toHaveText(
    ["Uploaded stop A", "Uploaded stop B"]);
});

test("adds Resilience Atlas basemaps and national borders only after authorization", async ({ page, request }) => {
  await page.goto(`/share/${await share(request)}#invalid`);
  await expect(page.locator("#status")).toContainText("invalid");
  expect(mapboxRequests).toHaveLength(0);
  const id = await share(request);

  await open(page, id);
  await expect.poll(() => mapboxRequests.some(({ url }) => url.includes("/styles/v1/cigrp/cixkh6jb000582smx8pfdeu23/"))).toBe(true);
  await expect.poll(() => mapboxRequests.some(({ url }) => url.includes("mapbox.mapbox-streets-v8"))).toBe(true);
  expect(mapboxRequests.every(({ url, referer }) => !url.includes(secret) && !referer)).toBe(true);
  await expect(page.locator(".ol-attribution")).toHaveCount(0);
  await expect(page.locator("#map-disclaimer")).toHaveText(
    /^© Mapbox © OpenStreetMap \| Disclaimer: The boundaries[^]*Terms of Use\s*Link expires: .+ UTC$/,
    { useInnerText: true });
  await expect(page.locator("header #expiry")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Disclaimer" })).toBeHidden();
  await expect(page.getByRole("link", { name: "© OpenStreetMap" })).toHaveAttribute(
    "href", "https://www.openstreetmap.org/copyright");
  await expect(page.locator("#map-disclaimer")).toContainText("do not imply official endorsement");
  await expect(page.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute(
    "href", "https://www.conservation.org/policies/privacy");
  await expect(page.getByRole("link", { name: "Terms of Use" })).toHaveAttribute(
    "href", "https://www.conservation.org/policies/terms-of-use");

  await page.getByRole("combobox", { name: "Basemap" }).selectOption("satellite");
  await expect.poll(() => mapboxRequests.some(({ url }) => url.includes("cizsz6pv700422ro73xdhzi1g"))).toBe(true);
  await page.getByRole("checkbox", { name: "National borders" }).uncheck();
  await expect(page.locator(".national-borders")).toBeHidden();
});

test("small screens show the map full screen and open the layer list as a sheet", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, await share(request));
  const sheet = page.locator("#controls");
  const layers = page.getByRole("button", { name: "Layers", exact: true });
  const showMap = page.getByRole("button", { name: "Show map" });
  const inert = (selector) => page.locator(selector).evaluate((element) => element.inert);
  const box = (selector) => page.locator(selector).boundingBox();

  await expect(page.locator(".powered-by")).toBeHidden();
  const main = await box("main");
  expect(main.y + main.height).toBeCloseTo(844, 0);
  expect(await box("#map")).toEqual(main);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth &&
    document.documentElement.scrollHeight <= innerHeight)).toBe(true);
  await expect(sheet).toBeHidden();
  expect(await inert("#controls")).toBe(true);
  await expect(layers).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#mobile-notice")).toBeVisible();

  await expect(page.locator("#expiry")).toBeVisible();
  await expect(page.locator("#expiry")).toContainText("UTC");
  await expect(page.locator("#disclaimer-text")).toBeHidden();
  const disclaimer = page.getByRole("button", { name: "Disclaimer" });
  await disclaimer.click();
  await expect(disclaimer).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#disclaimer-text")).toBeVisible();
  await expect(page.locator("#disclaimer-text")).toContainText("do not imply official endorsement");
  await disclaimer.click();
  await expect(page.locator("#disclaimer-text")).toBeHidden();

  await layers.click();
  await expect(sheet).toBeVisible();
  await expect(showMap).toBeFocused();
  await expect(showMap).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#mobile-notice")).toBeHidden();
  await expect.poll(() => box("#controls")).toEqual(main);
  expect(await inert("#map-region")).toBe(true);
  await expect(page.getByRole("checkbox", { name: "Raster result" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(layers).toBeFocused();

  await layers.click();
  await page.getByRole("button", { name: "Show full extent" }).click();
  await expect(sheet).toBeHidden();
  expect(await inert("#map-region")).toBe(false);

  const point = await page.locator(vectorCanvas).evaluate((canvas) => {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const rect = canvas.getBoundingClientRect();
    const mapRect = document.getElementById("map").getBoundingClientRect();
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] === 255 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 255) {
        const x = rect.left + (i / 4 % canvas.width) * rect.width / canvas.width;
        const y = rect.top + Math.floor(i / 4 / canvas.width) * rect.height / canvas.height;
        if (x > mapRect.left + 60 && x < mapRect.right - 60 &&
            y > mapRect.top + 80 && y < mapRect.bottom - 80) return { x, y };
      }
    }
  });
  await page.mouse.click(point.x + 3, point.y + 8);
  await expect(sheet).toBeVisible();
  await expect(page.locator("#selection-title")).toBeFocused();
  await expect(page.locator("#attributes")).toContainText("point");
  await showMap.click();
  await expect(sheet).toBeHidden();
  await expect(layers).toBeFocused();

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(sheet).toBeVisible();
  expect(await inert("#controls")).toBe(false);
  await expect(layers).toBeHidden();
  await expect(page.locator("#expiry")).toBeVisible();
});

test("the desktop sidebar is open by default and collapses to give the map the full width", async ({ page, request }) => {
  await open(page, await share(request));
  const sidebar = page.locator("#controls");
  const hide = page.getByRole("button", { name: "Hide layers" });
  const layers = page.getByRole("button", { name: "Layers", exact: true });
  const mapWidth = () => page.locator("#map canvas").first().evaluate((canvas) => canvas.getBoundingClientRect().width);
  await expect(sidebar).toBeVisible();
  await expect(layers).toBeHidden();
  await expect(hide).toHaveAttribute("aria-expanded", "true");
  const narrow = await mapWidth();

  await hide.click();
  await expect(sidebar).toBeHidden();
  await expect(layers).toBeFocused();
  await expect(layers).toHaveAttribute("aria-expanded", "false");
  await expect.poll(mapWidth).toBe(1280);
  await page.keyboard.press("Escape");
  await expect(sidebar).toBeHidden();

  await layers.click();
  await expect(sidebar).toBeVisible();
  await expect(hide).toBeFocused();
  await expect.poll(mapWidth).toBe(narrow);
  expect(narrow).toBeLessThan(1280);
});

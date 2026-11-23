import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

const secret = "a".repeat(43);
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
test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) => console.error(error.message));
  page.on("console", (msg) => { if (msg.type() === "error") console.error(msg.text()); });
});

async function share(request, options = {}) {
  const id = randomUUID();
  await request.post(`${fixtureUrl}/__share/${id}`, { data: options });
  return id;
}
async function open(page, id) {
  await page.goto(`/share/${id}#${secret}`);
  await expect(page.locator("#status")).toContainText("Map ready");
  await expect(page.locator(vectorCanvas).first()).toBeVisible();
}

test("renders authorized raster/vector tiles, legends, controls and safe attributes", async ({ page, request }) => {
  const id = await share(request);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await open(page, id);
  await expect(page.locator("#title")).toHaveText("Example shared analysis");
  await expect(page.locator("#expiry")).toContainText("UTC");
  await expect(page.locator("#layers")).toContainText("polygon (#ffff00)");
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
  const lineOption = await page.getByRole("combobox").locator("option").filter({ hasText: "(ID 2)" }).first().getAttribute("value");
  await page.getByRole("combobox").selectOption(lineOption);
  await expect(page.locator("#attributes")).toContainText("line");
  await page.getByRole("slider", { name: "Vector result opacity" }).fill("40");
  await expect.poll(() => colorCount(page, vectorCanvas, [255, 255, 0, 102])).toBeGreaterThan(50);
  await page.getByRole("checkbox", { name: "Vector result" }).uncheck();
  await expect(page.locator(vectorCanvas).first()).toBeHidden();
  await page.getByRole("checkbox", { name: "Vector result" }).check();
  await expect(page.locator(vectorCanvas).first()).toBeVisible();
  await page.locator("#map").focus();
  const beforeZoom = await page.locator("#coordinates").textContent();
  await page.keyboard.press("+");
  await expect(page.locator("#coordinates")).not.toHaveText(beforeZoom);
  await page.getByRole("button", { name: "Show full extent" }).click();
  expect(errors).toEqual([]);
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
  await expect(page.locator("#status")).toContainText("Map ready");
  await expect.poll(() => colorCount(page, vectorCanvas, [255, 255, 0, 255])).toBe(0);
});

test("removes fragment before requests and does not leak capabilities or send external requests", async ({ page, request, context }) => {
  const id = await share(request, { title: "<img src=x onerror=alert(1)>", attribution: "<script>alert(2)</script>" });
  const requests = [];
  page.on("request", (req) => requests.push(req));
  await open(page, id);
  expect(new URL(page.url()).hash).toBe("");
  await expect(page.locator("#title")).toHaveText("<img src=x onerror=alert(1)>");
  await expect(page.locator("#title img")).toHaveCount(0);
  const exchange = requests.find((req) => req.url().endsWith("/session"));
  expect(JSON.parse(exchange.postData()).secret).toBe(secret);
  for (const req of requests) {
    expect(new URL(req.url()).origin).toBe(new URL(page.url()).origin);
    expect(req.url()).not.toContain(secret);
    expect(req.headers().referer || "").toBe("");
    if (req !== exchange) expect(req.postData() || "").not.toContain(secret);
  }
  const cookie = (await context.cookies()).find((cookie) => cookie.name === "te_share_session");
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.secure).toBe(true);
  expect(cookie.path).toBe(`/api/v1/public-shares/${id}`);
  await page.reload();
  await expect(page.locator("#status")).toContainText("Map ready");
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
  await expect(page.locator("#status")).toContainText("Map ready");
});

test("clears displayed data when revoked and refresh cannot extend an expired session", async ({ page, request }) => {
  await page.clock.install();
  const id = await share(request, { sessionTtl: 60 });
  await open(page, id);
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
});

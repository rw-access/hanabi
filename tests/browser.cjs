"use strict";

const assert = require("node:assert/strict");
const { chromium, devices } = require("playwright");

const baseURL = process.env.HANABI_URL || "http://127.0.0.1:8765";
const teammate = process.env.HANABI_TEAMMATE || "Lantern";
assert.ok(["Lantern", "Adaptive"].includes(teammate));
const executablePath = process.env.CHROMIUM_EXECUTABLE;
const content = (page, selector) => page.locator(selector).textContent();
const button = (page, name) => page.getByRole("button", { name, exact: true });
const waitText = (page, selector, message) => page.waitForFunction(
  ([selector, message]) => document.querySelector(selector)?.textContent.includes(message),
  [selector, message],
);
const waitCount = (page, selector, count) => page.waitForFunction(
  ([selector, count]) => document.querySelectorAll(selector).length === count,
  [selector, count],
);

async function deal(page) {
  await page.goto(baseURL);
  const saveReplay = page.getByRole("checkbox", { name: "Save replays on this server" });
  if (await saveReplay.isVisible()) {
    assert.equal(await saveReplay.isChecked(), true);
    await saveReplay.uncheck(); // Keep automated games out of the human play archive.
  }
  await button(page, "Deal me in").click();
  await page.getByRole("heading", { name: "Fireworks" }).waitFor();
  assert.equal(await content(page, "#clues"), "8");
  assert.equal(await content(page, "#lives"), "3");
  assert.equal(await content(page, "#remaining"), "40");
  assert.equal(await content(page, "#teammate-name"), teammate);
  const partnerCard = new RegExp(`^${teammate} card \\d: (Red|Yellow|Green|Blue|White) [1-5]\\.`);
  assert.equal(await page.getByRole("img", { name: partnerCard }).count(), 5);
  assert.equal(await page.getByRole("button", { name: /^Your card \d: any color; any number$/ }).count(), 5);
  assert.equal(await page.locator("#hand .card-back").count(), 5);
  assert.equal(await page.locator("#hand [data-color], #hand .card-index").count(), 0);
  assert.equal(await page.locator("#fireworks .empty-stack").count(), 5);
  const partner = await page.locator("#partner").boundingBox();
  const stacks = await page.locator("#fireworks").boundingBox();
  const hand = await page.locator("#hand").boundingBox();
  assert.ok(partner.y + partner.height <= stacks.y && stacks.y + stacks.height <= hand.y,
    "The fireworks belong between the two hands");
}

async function desktop(page) {
  await deal(page);
  await button(page, "How to play").click();
  const rules = page.getByRole("dialog", { name: "How to play" });
  await rules.waitFor();
  await page.keyboard.press("Escape");
  await rules.waitFor({ state: "hidden" });
  assert.equal(await page.locator(":focus").getAttribute("id"), "rules");

  const choice = page.getByRole("group", { name: "Colors" }).getByRole("button").first();
  const colorName = await choice.textContent();
  const color = await choice.getAttribute("data-value");
  const matches = await page.locator(`#partner [data-color="${color}"]`).count();
  assert.ok(matches > 0);
  await choice.click();
  assert.equal(await choice.getAttribute("aria-pressed"), "true");
  assert.equal(await page.locator("#partner .clue-match").count(), matches);
  assert.equal(await page.locator("#partner .clue-miss").count(), 5 - matches);
  // The same focused chip moves between the sidebar and the phone disclosure.
  for (const [width, panel] of [[390, "mobile-clue-panel"], [1280, "desktop-clue-panel"]]) {
    await page.setViewportSize({ width, height: 800 });
    await page.waitForFunction((id) => document.getElementById(id).contains(document.activeElement), panel);
    assert.equal(await choice.evaluate((chip) => chip === document.activeElement), true);
    assert.equal(await choice.getAttribute("aria-pressed"), "true");
  }
  assert.match(await content(page, "#clue-desc"), /will mark card/);
  assert.equal(await content(page, "#clues"), "8");
  await button(page, `Give ${colorName} clue`).click();
  await waitCount(page, "#history li", 2);
  const clueMoves = await page.locator("#history li span").allTextContents();
  assert.ok(clueMoves[0].startsWith(`${teammate} `));
  assert.ok(clueMoves[1].startsWith(`You clued ${colorName}`));
  assert.equal(await content(page, "#turn"), "Your turn");

  const deck = Number(await content(page, "#remaining"));
  const selected = page.getByRole("button", { name: /^Your card 1:/ });
  await selected.click();
  assert.equal(await selected.getAttribute("aria-pressed"), "true");
  assert.match(await content(page, "#selection"), /^Card 1:/);
  await button(page, "Play card").click();
  await waitText(page, "#history", "played ");
  await page.waitForFunction(() => [...document.querySelectorAll("#history li span")]
    .some((item) => /^You (mis)?played /.test(item.textContent)));
  assert.equal(await page.locator("#hand button").count(), 5);
  assert.ok(Number(await content(page, "#remaining")) < deck);
  const moves = await page.locator("#history li").allTextContents();
  await page.reload();
  await waitCount(page, "#history li", moves.length);
  assert.deepEqual(await page.locator("#history li").allTextContents(), moves);

  await button(page, "New game").click();
  const restart = page.getByRole("dialog", { name: "Start over?" });
  await restart.waitFor();
  const saveReplay = restart.getByRole("checkbox", { name: "Save replays on this server" });
  if (await saveReplay.isVisible()) {
    assert.equal(await saveReplay.isChecked(), false);
    assert.equal(await content(page, "#recording-status"), "Replay saving is off for this deal.");
  }
  await button(page, "Keep playing").click();
  await restart.waitFor({ state: "hidden" });
  assert.deepEqual(await page.locator("#history li").allTextContents(), moves);
  await button(page, "New game").click();
  await button(page, "Deal a new game").click();
  await restart.waitFor({ state: "hidden" });
  await waitCount(page, "#history li", 0);
  assert.equal(await content(page, "#remaining"), "40");
  assert.equal(await content(page, "#clues"), "8");
}

async function mobile(page) {
  await deal(page);
  const dimensions = () => page.locator("html").evaluate((element) => ({
    viewport: element.clientWidth,
    content: element.scrollWidth,
  }));
  const { viewport, content: width } = await dimensions();
  assert.equal(viewport, 390);
  assert.ok(width <= viewport, `Horizontal overflow: viewport ${viewport}, page ${width}`);
  await page.locator("#mobile-clue-panel > summary").tap();
  const choice = page.getByRole("group", { name: "Numbers" }).getByRole("button").first();
  const number = await choice.textContent();
  await choice.tap();
  const cards = await page.locator("#partner").boundingBox();
  const send = await page.locator("#send-clue").boundingBox();
  assert.ok(cards.y >= 0 && send.y + send.height <= page.viewportSize().height,
    "The partner hand and clue confirmation should be visible together on a phone");
  await button(page, `Give ${number} clue`).tap();
  await waitCount(page, "#history li", 2);
  await page.getByRole("button", { name: /^Your card 1:/ }).tap();
  await button(page, "Play card").tap();
  await page.waitForFunction(() => [...document.querySelectorAll("#history li span")]
    .some((item) => /^You (mis)?played /.test(item.textContent)));
  const updated = await dimensions();
  assert.ok(updated.content <= updated.viewport, "Horizontal overflow after moves");
}

(async () => {
  const browser = await chromium.launch({ chromiumSandbox: true, executablePath });
  try {
    for (const [name, flow, config] of [
      ["Desktop", desktop, { viewport: { width: 1280, height: 800 } }],
      ["Mobile (390px)", mobile, { ...devices["iPhone 13"], defaultBrowserType: "chromium" }],
    ]) {
      const context = await browser.newContext(config);
      const page = await context.newPage();
      page.setDefaultTimeout(8000);
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("response", (response) => {
        if (["script", "stylesheet", "image"].includes(response.request().resourceType()) && !response.ok()) {
          errors.push(`${response.status()} ${response.url()}`);
        }
      });
      await flow(page);
      assert.deepEqual(errors, []);
      await context.close();
      console.log(`${name} smoke test passed`);
    }
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

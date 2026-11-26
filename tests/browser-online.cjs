"use strict";

const assert = require("node:assert/strict");
const { chromium, devices } = require("playwright");

const base = process.env.HANABI_URL || "http://127.0.0.1:8787/play/hanabi/";
const button = (page, name) => page.getByRole("button", { name, exact: true });
const text = (page, selector) => page.locator(selector).textContent();
const waitTurn = (page, label) => page.waitForFunction(
  (label) => document.querySelector("#turn").textContent === label, label);
const waitMoves = (page, count) => page.waitForFunction(
  (count) => document.querySelectorAll("#history li").length === count, count);

async function state(page, code) {
  const response = await page.request.get(new URL(`api/rooms/${code}`, page.url()).href);
  assert.equal(response.status(), 200);
  return response.json();
}

function inspect(snapshot, seat) {
  assert.equal(snapshot.seat, seat);
  assert.equal(snapshot.game.seat, seat);
  const wire = JSON.stringify(snapshot);
  assert.doesNotMatch(wire, /"(draw_order|seed|token|seats|training_hand)"\s*:/);
  for (const card of snapshot.game.hand) {
    assert.deepEqual(Object.keys(card).sort(), ["colors", "ranks"]);
  }
}

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, chromiumSandbox: true });
  try {
    const host = await browser.newPage();
    const guest = await browser.newPage({ ...devices["iPhone 13"], defaultBrowserType: "chromium" });
    const observer = await browser.newPage();
    const errors = [];
    for (const page of [host, guest, observer]) {
      page.setDefaultTimeout(12000);
      page.on("pageerror", (error) => errors.push(error.message));
    }
    await host.goto(base);
    await button(host, "Create room").click();
    await host.locator("#waiting-room").waitFor();
    const code = await text(host, "#invite-code");
    const invite = await host.locator("#invite-link").inputValue();
    assert.equal(new URL(invite).search, `?room=${code}`);
    assert.equal((await state(host, code)).game, null);

    await guest.goto(invite);
    await guest.locator("#join-seat:enabled").waitFor();
    assert.equal(await guest.locator("#room-code").inputValue(), code);
    assert.equal((await state(host, code)).status, "waiting");
    await button(guest, "Join room").click();
    await Promise.all([host.locator("#table").waitFor(), guest.locator("#table").waitFor()]);
    await waitTurn(host, "Your turn");
    await waitTurn(guest, "Teammate’s turn");
    inspect(await state(host, code), 0);
    inspect(await state(guest, code), 1);
    assert.equal(await guest.locator("#hand .card-back").count(), 5);

    await observer.goto(invite);
    await button(observer, "Join room").click();
    await observer.locator("#error").waitFor();
    assert.equal(await observer.locator("#table").isVisible(), false);
    const sockets = [];
    guest.on("websocket", (socket) => sockets.push(new URL(socket.url()).pathname));
    await guest.reload();
    await guest.locator("#table").waitFor();
    assert.equal((await state(guest, code)).seat, 1);

    await host.locator('#clue-choices button:not(.empty-clue)').first().click();
    await host.locator("#send-clue").click();
    await waitTurn(guest, "Your turn");
    assert.match(await text(guest, "#incoming-clue"), /^Teammate clued /);
    await guest.locator("#mobile-clue-panel > summary").click();
    await guest.locator('#clue-choices button:not(.empty-clue)').first().click();
    await guest.locator("#send-clue").click();
    await waitTurn(host, "Your turn");
    assert.match(await text(host, "#incoming-clue"), /^Teammate clued /);
    assert.match(await text(guest, "#history li:first-child span"), /^You clued /);
    assert.ok(await guest.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
    assert.ok(sockets.some((path) => path.endsWith(`/${code}/socket`)));

    for (let turn = 2; turn < 54; turn++) {
      const actor = turn % 2 ? guest : host;
      if (await actor.locator("#outcome").isVisible()) break;
      await waitTurn(actor, "Your turn");
      await actor.locator("#hand button").first().click();
      await button(actor, "Play card").click();
      await Promise.all([waitMoves(host, turn + 1), waitMoves(guest, turn + 1)]);
    }
    await Promise.all([host.locator("#outcome").waitFor(), guest.locator("#outcome").waitFor()]);
    assert.equal(await text(host, "#outcome-body"), await text(guest, "#outcome-body"));

    await observer.goto(base);
    await button(observer, "Play with Adaptive").click();
    await observer.locator("#table").waitFor();
    assert.equal(await text(observer, "#teammate-name"), "Adaptive");
    await observer.locator("#hand button").first().click();
    await button(observer, "Play card").click();
    await waitMoves(observer, 2);
    await observer.reload();
    await observer.locator("#table").waitFor();
    assert.equal(await observer.locator("#history li").count(), 2);
    assert.deepEqual(errors, []);
    console.log("Passed two-seat room creation, explicit join, privacy, reconnect, full deal and Adaptive solo play");
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

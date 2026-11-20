"use strict";

const $ = (id) => document.getElementById(id);
const colors = ["red", "yellow", "green", "blue", "white"];
const ranks = [1, 2, 3, 4, 5];
const cap = (value) => value[0].toUpperCase() + value.slice(1);
const cardName = ({ color, rank }) => `${cap(color)} ${rank}`;

let game = null;
let busy = false;
let online = false;
let selected = null;
let clue = null;

function el(tag, text = "", className = "") {
  const elem = document.createElement(tag);
  elem.textContent = text;
  if (className) elem.className = className;
  return elem;
}

function nameClue({ kind, value }) {
  return kind === "color" ? cap(value) : String(value);
}

function sameClue(a, b) {
  return a && b && a.kind === b.kind && a.value === b.value;
}

function eventsText(event) {
  const who = event.player === 0 ? "You" : "Lantern";
  if (event.type === "hint") {
    const slots = event.matched.map((i) => i + 1).join(", ");
    return `${who} clued ${nameClue(event)} (card${event.matched.length === 1 ? "" : "s"} ${slots}).`;
  }
  const card = cardName(event.card);
  if (event.type === "discard") return `${who} discarded ${card}.`;
  return event.success ? `${who} played ${card}.` : `${who} misplayed ${card}. One fuse lost.`;
}

function accessibleKnowledge(knowledge) {
  const knownColors = knowledge.colors.length === 5 ? "any color" : knowledge.colors.map(cap).join(" or ");
  const knownRanks = knowledge.ranks.length === 5 ? "any number" : knowledge.ranks.join(" or ");
  return `${knownColors}; ${knownRanks}`;
}

function briefKnowledge(knowledge) {
  const knownColors = knowledge.colors.length === 5 ? "" : knowledge.colors.map((color) => cap(color).slice(0, 1)).join("·");
  const knownRanks = knowledge.ranks.length === 5 ? "" : knowledge.ranks.join("·");
  return [knownColors, knownRanks].filter(Boolean).join(" / ");
}

function selectCard(index, button) {
  selected = selected === index ? null : index;
  clue = null;
  updateControls();
  button.focus();
}

function renderHand(partner) {
  const cards = partner ? game.partner : game.hand;
  const container = $(partner ? "partner" : "hand");
  const items = cards.map((entry, index) => {
    const slot = index + 1;
    const knowledge = partner ? entry.knowledge : entry;
    const face = el(partner ? "div" : "button", "", `card${partner ? "" : " hidden-card"}`);
    const brief = briefKnowledge(knowledge);
    const full = accessibleKnowledge(knowledge);
    if (partner) {
      face.dataset.color = entry.color;
      face.setAttribute("role", "img");
      face.setAttribute("aria-label", `Lantern card ${slot}: ${cardName(entry)}. Its clues: ${full}.`);
      face.append(el("b", entry.rank), el("small", cap(entry.color)));
      if (brief) face.append(el("small", brief, "deduction"));
      face.title = `Lantern's clues: ${full}`;
    } else {
      face.type = "button";
      face.dataset.index = index;
      face.setAttribute("aria-label", `Your card ${slot}: ${full}`);
      face.title = `Clues: ${full}`;
      face.append(el("b", knowledge.ranks.length === 1 ? knowledge.ranks[0] : "?", ""), el("span", `#${slot}`, "slot"));
      if (brief) face.append(el("small", brief, "deduction"));
      face.addEventListener("click", () => selectCard(index, face));
    }
    const item = el("li");
    item.append(face);
    return item;
  });
  container.replaceChildren(...items);
}

function renderClues() {
  const groups = ["color", "rank"].map((kind) => {
    const group = el("div", "", "clue-group");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", kind === "color" ? "Colors" : "Numbers");
    for (const action of game.legal.filter((option) => option.type === "hint" && option.kind === kind)) {
      const chip = el("button", nameClue(action), "chip");
      chip.type = "button";
      chip.dataset.kind = action.kind;
      chip.dataset.value = action.value;
      if (kind === "color") chip.dataset.color = action.value;
      chip.setAttribute("aria-label", kind === "color" ? `Color: ${nameClue(action)}` : `Number: ${action.value}`);
      chip.addEventListener("click", () => {
        clue = sameClue(clue, action) ? null : action;
        if (clue) selected = null;
        updateControls();
      });
      group.append(chip);
    }
    return group;
  });
  $("clue-choices").replaceChildren(...groups);
}

function updateControls() {
  $("retry").disabled = busy;
  $("deal").disabled = busy || !online;
  $("restart").disabled = busy || !online;
  $("deal").textContent = busy ? "Loading table…" : "Deal me in";
  if (!game) return;

  const canMove = online && !busy && !game.over && game.current === game.seat;
  const clueReady = game.legal.some((action) => sameClue(clue, action));
  const discardReady = game.legal.some((action) => action.type === "discard" && action.index === selected);
  const playReady = game.legal.some((action) => action.type === "play" && action.index === selected);
  $("card-controls").hidden = game.over;
  $("clue-controls").hidden = game.over;
  $("play").disabled = !canMove || !playReady;
  $("discard").disabled = !canMove || !discardReady;
  $("send-clue").disabled = !canMove || !clueReady;
  $("turn").textContent = game.over ? "Game over" : busy ? "Updating…" : game.current === game.seat ? "Your turn" : "Lantern’s turn";
  $("selection").textContent = selected === null ? "Select a card to play or discard." : `Card ${selected + 1}: ${accessibleKnowledge(game.hand[selected])}.`;
  if (selected !== null && game.clues === 8) {
    $("selection").textContent += " You can’t discard with 8 clues.";
  }
  for (const card of $("hand").querySelectorAll("button")) {
    card.disabled = !canMove;
    card.setAttribute("aria-pressed", String(Number(card.dataset.index) === selected));
  }

  for (const choice of $("clue-choices").querySelectorAll("button")) {
    choice.disabled = !canMove;
    const pressed = clue && clue.kind === choice.dataset.kind && String(clue.value) === choice.dataset.value;
    choice.setAttribute("aria-pressed", String(Boolean(pressed)));
  }
  const slots = [];
  for (const [index, face] of Array.from($("partner").querySelectorAll(".card")).entries()) {
    const match = Boolean(clue && game.partner[index][clue.kind] === clue.value);
    face.classList.toggle("clue-match", match);
    if (match) slots.push(index + 1);
  }
  if (clue) {
    const targets = slots.length === 1 ? `card ${slots[0]}` : `cards ${slots.join(", ")}`;
    $("clue-desc").textContent = `${nameClue(clue)} will mark ${targets}.`;
    $("send-clue").textContent = `Give ${nameClue(clue)} clue`;
  } else {
    $("clue-desc").textContent = game.clues === 0
      ? "No clue tokens left. Discard a card to earn one."
      : "Select a color or number to mark every matching card.";
    $("send-clue").textContent = "Give clue";
  }
}

function render() {
  const showing = game !== null;
  $("table").hidden = !showing;
  $("welcome").hidden = showing;
  $("restart").hidden = !showing;
  if (!showing) {
    updateControls();
    return;
  }
  $("stacks-score").replaceChildren(document.createTextNode(game.score), el("small", " / 25"));
  $("stacks-score").setAttribute("aria-label", `Team score: ${game.score} out of 25`);
  $("fireworks").replaceChildren(...colors.map((color) => {
    const top = game.fireworks[color];
    const stack = el("div", "", "firework");
    stack.dataset.color = color;
    stack.setAttribute("aria-label", `${cap(color)} stack: ${top} of 5`);
    stack.title = top < 5 ? `Next: ${cap(color)} ${top + 1}` : `${cap(color)} complete`;
    stack.append(el("small", cap(color)), el("b", top === 0 ? "·" : top === 5 ? "✷" : top));
    return stack;
  }));
  for (const key of ["clues", "lives", "remaining"]) $(key).textContent = game[key];
  $("countdown").hidden = game.over || game.final_turns === null;
  if (!$("countdown").hidden) {
    const n = game.final_turns;
    $("countdown").textContent = `The deck is empty. ${n} turn${n === 1 ? "" : "s"} left for the team.`;
  }
  $("outcome").hidden = !game.over;
  if (game.over) {
    $("outcome-title").textContent = game.end === "perfect" ? "A perfect show" : game.end === "fuse" ? "The fuses ran out" : "That’s a wrap";
    const built = Object.values(game.fireworks).reduce((sum, top) => sum + top, 0);
    $("outcome-body").textContent = game.end === "fuse"
      ? `You built ${built} points of fireworks, but the official team score is 0 of 25.`
      : `You and Lantern scored ${game.score} out of 25 together.`;
  }

  renderHand(true);
  renderHand(false);
  renderClues();

  $("history").replaceChildren(...game.history.slice().reverse().map((event) => {
    const item = el("li");
    const turn = el("small", event.turn);
    turn.setAttribute("aria-label", `Turn ${event.turn}`);
    item.append(turn, el("span", eventsText(event)));
    return item;
  }));
  $("empty-history").hidden = game.history.length !== 0;
  $("discard-list").replaceChildren(...game.discard.map((card) => {
    const item = el("li", cardName(card));
    item.dataset.color = card.color;
    return item;
  }));
  $("empty-discard").hidden = game.discard.length !== 0;
  updateControls();
}

async function api(path, body) {
  const init = { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(8000) };
  if (body !== undefined) {
    init.method = "POST";
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(path, init);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const result = await response.json();
  if (!result || !Object.hasOwn(result, "game")) throw new Error("Missing game state");
  return result.game;
}

function notice(message = "") {
  $("error").hidden = !message;
  $("error-text").textContent = message;
}

function applyGame(next) {
  game = next;
  clue = null;
  selected = null;
  online = true;
}

async function refresh() {
  if (busy) return;
  busy = true;
  updateControls();
  try {
    applyGame(await api("/api/game"));
    notice();
  } catch {
    online = false;
    notice("Couldn’t reach the game server.");
  } finally {
    busy = false;
    render();
  }
}

async function write(path, body) {
  if (busy || !online) return;
  busy = true;
  const oldTurn = game?.turn ?? 0;
  const oldId = game?.id;
  updateControls();
  try {
    applyGame(await api(path, body));
    notice();
    const summary = game.history.filter((event) => event.turn > oldTurn).map(eventsText).join(" ");
    $("announcement").textContent = path === "/api/new"
      ? "A new game is dealt. Your turn." : `${summary} ${game.over ? "Game over." : "Your turn."}`;
  } catch {
    // GET only: resync the current revision without applying a move twice.
    try {
      applyGame(await api("/api/game"));
      notice(game === null
        ? "This table has expired. You can deal a new game."
        : "The server didn’t confirm your request. The latest table is shown below; no action was retried.");
    } catch {
      online = false;
      notice("The connection dropped. Refresh the table before making another move.");
    }
  } finally {
    busy = false;
    render();
    if (!$("error").hidden) $("retry").focus();
    else if (game?.over) $("outcome-title").focus();
    else if (game && oldId !== game.id) $("table-title").focus();
    else if (game) $("hand").querySelector("button:not(:disabled)")?.focus({ preventScroll: true });
  }
}

function submit(action) {
  if (!game || !action) return;
  void write("/api/action", { id: game.id, turn: game.turn, action });
}

$("deal").addEventListener("click", () => void write("/api/new", {}));
$("restart").addEventListener("click", () => $("restart-dialog").showModal());
$("confirm-restart").addEventListener("click", () => {
  $("restart-dialog").close();
  void write("/api/new", {});
});
for (const button of document.querySelectorAll("button[data-close]")) {
  button.addEventListener("click", () => $(button.dataset.close).close());
}
$("retry").addEventListener("click", () => void refresh());
$("rules").addEventListener("click", () => $("rules-dialog").showModal());
$("play").addEventListener("click", () => submit(game.legal.find((a) => a.type === "play" && a.index === selected)));
$("discard").addEventListener("click", () => submit(game.legal.find((a) => a.type === "discard" && a.index === selected)));
$("send-clue").addEventListener("click", () => submit(game.legal.find((a) => sameClue(clue, a))));
void refresh();

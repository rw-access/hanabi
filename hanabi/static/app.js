"use strict";

const $ = (id) => document.getElementById(id);
const teammateName = $("teammate-name").textContent;
const recordingSettings = $("recording-settings");
const saveReplay = $("save-replay");
const recordingEnabled = !recordingSettings.hidden;
if (recordingEnabled) {
  try {
    saveReplay.checked = localStorage.getItem("hanabi.save-replays") !== "off";
  } catch {
    // Browser storage can be disabled. The checkbox still applies to this deal.
  }
}
const colors = ["red", "yellow", "green", "blue", "white"];
const ranks = [1, 2, 3, 4, 5];
const cap = (value) => value[0].toUpperCase() + value.slice(1);
const cardName = ({ color, rank }) => `${cap(color)} ${rank}`;
// Each suit has its own printing mark so the deck also reads without color.
const suitPaths = {
  red: "M12 8C7 1 3 7 7 11C0 11 2 18 8 16C6 23 13 23 14 17C19 22 24 16 18 13C25 10 20 4 15 8C15 1 8 1 12 8Z",
  yellow: "M12 1L14 6L19 4L18 9L23 12L18 14L20 20L14 18L12 23L9 18L4 20L6 14L1 12L6 9L4 4L10 6Z",
  green: "M12 1L18 9H15L21 15H17L23 20H14V23H10V20H1L7 15H3L9 9H6Z",
  blue: "M1 10C5 10 7 4 12 4C16 4 18 7 18 10C16 8 14 8 13 9C12 11 15 13 18 12C20 12 22 10 23 9V13C20 17 16 17 12 14C8 11 5 15 1 15ZM1 18C5 18 8 14 12 17C16 20 19 20 23 17V20C19 23 15 23 11 20C8 18 5 22 1 21Z",
  white: "M10 2C11 9 13 11 19 12C13 13 11 16 10 23C9 16 7 13 1 12C7 11 9 9 10 2ZM19 1L20.4 4.6L24 6L20.4 7.4L19 11L17.6 7.4L14 6L17.6 4.6Z",
};

function suitMark(color) {
  const ns = "http://www.w3.org/2000/svg";
  const mark = document.createElementNS(ns, "svg");
  mark.setAttribute("viewBox", "0 0 24 24");
  mark.setAttribute("aria-hidden", "true");
  mark.setAttribute("class", "suit-mark");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", suitPaths[color]);
  mark.append(path);
  return mark;
}

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

function absentValue({ kind, value }) {
  return kind === "color" ? value : `${value}s`;
}

function sameClue(a, b) {
  return a && b && a.kind === b.kind && a.value === b.value;
}

function eventsText(event) {
  const who = event.player === 0 ? "You" : teammateName;
  if (event.type === "hint") {
    if (event.matched.length === 0) {
      const receiver = event.player === 0 ? `${teammateName}’s hand` : "your hand";
      return `${who} gave “No ${absentValue(event)}”. No cards in ${receiver} matched.`;
    }
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

function knowledgeLine(values, all, format) {
  if (values.length === all.length) return "";
  if (values.length === 1) return String(format(values[0], true));
  const excluded = all.filter((value) => !values.includes(value));
  const shorter = excluded.length < values.length;
  const options = shorter ? excluded : values;
  const labels = options.map((value) => format(value, options.length === 1)).join("·");
  return shorter ? `No ${labels}` : labels;
}

function clueSlip(knowledge) {
  const color = knowledgeLine(knowledge.colors, colors, (value, exact) => exact ? cap(value) : cap(value)[0]);
  const rank = knowledgeLine(knowledge.ranks, ranks, (value) => value);
  if (!color && !rank) return null;
  const slip = el("span", "", "clue-slip");
  slip.setAttribute("aria-hidden", "true");
  if (color) slip.append(el("span", color));
  if (rank) slip.append(el("span", rank));
  return slip;
}

function printFace(face, color, rank) {
  face.dataset.color = color;
  const corner = el("span", "", "card-corner");
  corner.append(el("b", rank, "card-index"), suitMark(color));
  face.append(el("span", "", "card-art"), corner, el("small", cap(color), "card-suit"));
}

const suitOf = (id) => colors[Math.floor(id / ranks.length)];
const rankOf = (id) => id % ranks.length + 1;

function cardOptions(possible) {
  return {
    colors: colors.filter((color) => possible.some((id) => suitOf(id) === color)),
    ranks: ranks.filter((rank) => possible.some((id) => rankOf(id) === rank)),
  };
}

function possibilitySlip(options) {
  const slip = el("span", "", "possible-slip");
  slip.setAttribute("aria-hidden", "true");
  const suits = el("span", "", "possible-suits");
  for (const color of options.colors) {
    const suit = el("span");
    suit.dataset.color = color;
    suit.title = cap(color);
    suit.append(suitMark(color));
    if (options.colors.length === 1) suit.append(cap(color));
    suits.append(suit);
  }
  const numbers = el("span", "", "possible-numbers");
  numbers.append(...options.ranks.map((rank) => el("span", rank)));
  slip.append(suits, numbers);
  return slip;
}

function percent(probability) {
  const rounded = Math.round(probability * 100);
  if (rounded === 0 && probability > 0) return "<1%";
  if (rounded === 100 && probability < 1) return ">99%";
  return `${rounded}%`;
}

function renderOdds() {
  $("card-odds").hidden = selected === null;
  if (selected === null) return;
  const { possible, probabilities } = game.beliefs[selected];
  const odds = new Map(possible.map((id, i) => [id, probabilities[i]]));
  const playable = possible.filter((id) => rankOf(id) === game.fireworks[suitOf(id)] + 1);
  $("odds-title").textContent = `Card ${selected + 1} odds`;
  $("play-odds").textContent = playable.length === possible.length ? "Every possibility plays safely."
    : !playable.length ? "No possibility plays now."
    : `${percent(playable.reduce((sum, id) => sum + odds.get(id), 0))} chance of a successful play.`;
  $("identity-odds").replaceChildren(...colors.map((color, colorIndex) => {
    const row = el("tr");
    const suit = el("th", cap(color));
    suit.scope = "row";
    suit.dataset.color = color;
    suit.prepend(suitMark(color));
    row.append(suit);
    for (const rank of ranks) {
      const probability = odds.get(colorIndex * ranks.length + rank - 1);
      const cell = el("td", probability === undefined ? "—" : percent(probability));
      if (probability === undefined) {
        cell.className = "impossible";
        cell.setAttribute("aria-label", "Impossible");
      }
      row.append(cell);
    }
    return row;
  }));
}

function selectCard(index, button) {
  selected = selected === index ? null : index;
  clue = null;
  updateControls();
  button.focus();
}

function renderHand(partner, incoming = null) {
  const cards = partner ? game.partner : game.hand;
  const container = $(partner ? "partner" : "hand");
  const items = cards.map((entry, index) => {
    const slot = index + 1;
    const knowledge = partner ? entry.knowledge : cardOptions(game.beliefs[index].possible);
    const face = el(partner ? "div" : "button", "", `card${partner ? "" : " hidden-card"}`);
    const full = accessibleKnowledge(knowledge);
    const item = el("li");
    if (partner) {
      face.setAttribute("role", "img");
      face.setAttribute("aria-label", `${teammateName} card ${slot}: ${cardName(entry)}. Its clues: ${full}.`);
      printFace(face, entry.color, entry.rank);
      face.title = `${teammateName}'s clues: ${full}`;
    } else {
      face.type = "button";
      face.dataset.index = index;
      face.setAttribute("aria-label", `Your card ${slot}: ${full}`);
      face.setAttribute("aria-controls", "card-odds");
      face.title = `Possible: ${full}`;
      face.append(el("span", "", "card-art card-back"), possibilitySlip(knowledge));
      if (incoming?.matched.includes(index)) {
        const ribbon = el("span", "", "clue-ribbon");
        ribbon.setAttribute("aria-hidden", "true");
        if (incoming.kind === "color") ribbon.append(suitMark(incoming.value));
        else ribbon.textContent = nameClue(incoming);
        face.append(ribbon);
        face.setAttribute("aria-describedby", "incoming-clue");
      }
      face.addEventListener("click", () => selectCard(index, face));
    }
    item.append(face);
    const slotLabel = el("span", String(slot), "slot");
    slotLabel.setAttribute("aria-hidden", "true");
    item.append(slotLabel);
    if (partner) {
      const slip = clueSlip(knowledge);
      if (slip) item.append(slip);
    }
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
      const empty = !game.partner.some((card) => card[kind] === action.value);
      const label = empty ? `No ${absentValue(action)}` : nameClue(action);
      const chip = el("button", label, empty ? "chip empty-clue" : "chip");
      chip.type = "button";
      chip.dataset.kind = action.kind;
      chip.dataset.value = action.value;
      if (kind === "color") {
        chip.dataset.color = action.value;
        chip.prepend(suitMark(action.value));
      }
      chip.setAttribute("aria-label", empty ? label : kind === "color" ? `Color: ${label}` : `Number: ${label}`);
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

const narrowTable = window.matchMedia("(max-width: 740px)");
function positionClues() {
  const panel = $("mobile-clue-panel");
  const desktop = $("desktop-clue-panel");
  const controls = $("clue-controls");
  const active = document.activeElement;
  const focusedControl = controls.contains(active);
  const focusedSummary = panel.querySelector("summary") === active;
  const target = narrowTable.matches ? panel : desktop;
  target.hidden = Boolean(game?.over);
  if (narrowTable.matches && focusedControl) panel.open = true;
  target.append(controls);
  if (focusedControl) active.focus({ preventScroll: true });
  else if (focusedSummary && !narrowTable.matches) controls.querySelector("button:enabled")?.focus({ preventScroll: true });
  (narrowTable.matches ? desktop : panel).hidden = true;
}
narrowTable.addEventListener("change", positionClues);
positionClues();

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
  $("mobile-clue-panel").hidden = !narrowTable.matches || game.over;
  $("desktop-clue-panel").hidden = narrowTable.matches || game.over;
  $("play").disabled = !canMove || !playReady;
  $("discard").disabled = !canMove || !discardReady;
  $("send-clue").disabled = !canMove || !clueReady;
  $("turn").textContent = game.over ? "Game over" : busy ? "Updating…" : game.current === game.seat ? "Your turn" : `${teammateName}’s turn`;
  $("selection").textContent = selected === null ? "Select a card to play or discard."
    : `Card ${selected + 1}: ${accessibleKnowledge(cardOptions(game.beliefs[selected].possible))}.`;
  if (selected !== null && game.clues === 8) {
    $("selection").textContent += " You can’t discard with 8 clues.";
  }
  renderOdds();
  for (const card of $("hand").querySelectorAll("button")) {
    card.disabled = busy;
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
    face.classList.toggle("clue-miss", Boolean(clue) && !match);
    if (match) slots.push(index + 1);
  }
  if (clue) {
    const targets = slots.length === 1 ? `card ${slots[0]}` : `cards ${slots.join(", ")}`;
    $("clue-desc").textContent = slots.length ? `${nameClue(clue)} will mark ${targets}.`
      : `No cards match. This rules out ${nameClue(clue)} for every card in ${teammateName}’s current hand.`;
    $("send-clue").textContent = slots.length ? `Give ${nameClue(clue)} clue` : `Give “No ${absentValue(clue)}” clue`;
  } else {
    $("clue-desc").textContent = game.clues === 0
      ? "No clue tokens left. Discard a card to earn one."
      : "Choose a clue to preview it. Each clue costs one token.";
    $("send-clue").textContent = "Give clue";
  }
}

function render() {
  const showing = game !== null;
  $("opening-table").hidden = true;
  $("table").hidden = !showing;
  $("welcome").hidden = showing || !online;
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
    stack.dataset.top = top;
    stack.setAttribute("role", "img");
    stack.setAttribute("aria-label", `${cap(color)} stack: ${top} of 5`);
    stack.title = top < 5 ? `Next: ${cap(color)} ${top + 1}` : `${cap(color)} complete`;
    if (top === 0) {
      stack.classList.add("empty-stack");
      stack.append(suitMark(color), el("small", cap(color), "stack-suit"));
    } else {
      stack.classList.add("card", "stack-face");
      printFace(stack, color, top);
    }
    return stack;
  }));
  for (const key of ["clues", "lives", "remaining"]) $(key).textContent = game[key];
  for (const [selector, total, available] of [[".clue-tokens", 8, game.clues], [".fuse-tokens", 3, game.lives]]) {
    const row = document.querySelector(selector);
    row.replaceChildren(...Array.from({ length: total }, (_, index) => el("i", "", index < available ? "full" : "spent")));
  }
  document.querySelector(".resources .stock").classList.toggle("empty-stock", game.remaining === 0);
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
      : `You and ${teammateName} scored ${game.score} out of 25 together.`;
  }

  const latest = game.history.at(-1);
  const incoming = latest?.type === "hint" && latest.player !== game.seat ? latest : null;
  const message = $("incoming-clue");
  message.hidden = !incoming;
  message.textContent = incoming ? eventsText(incoming) : "";
  renderHand(true);
  renderHand(false, incoming);
  renderClues();

  $("history").replaceChildren(...game.history.slice().reverse().map((event) => {
    const item = el("li");
    const turn = el("small", event.turn);
    turn.setAttribute("aria-label", `Turn ${event.turn}`);
    item.append(turn, el("span", eventsText(event)));
    return item;
  }));
  $("empty-history").hidden = game.history.length !== 0;
  $("recording-status").hidden = !recordingEnabled;
  $("recording-status").textContent = game.recording ? "Saving this replay on the server." : "Replay saving is off for this deal.";
  $("discard-list").replaceChildren(...game.discard.map((card) => {
    const item = el("li", cardName(card));
    item.dataset.color = card.color;
    return item;
  }));
  $("empty-discard").hidden = game.discard.length !== 0;
  updateControls();
}

class ApiError extends Error {}

async function api(path, body) {
  const init = { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(8000) };
  if (body !== undefined) {
    init.method = "POST";
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    const failed = await response.json().catch(() => ({}));
    throw new ApiError(failed.error || `The server returned HTTP ${response.status}.`);
  }
  const result = await response.json();
  if (!result || !Object.hasOwn(result, "game")) throw new Error("Missing game state");
  return result.game;
}

function notice(message = "") {
  $("error").hidden = !message;
  $("error-text").textContent = message;
}

function applyGame(next) {
  if (next?.id !== game?.id) $("mobile-clue-panel").open = false;
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
    applyGame(await api("api/game"));
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
    $("announcement").textContent = path === "api/new"
      ? "A new game is dealt. Your turn." : `${summary} ${game.over ? "Game over." : "Your turn."}`;
  } catch (error) {
    // GET only: resync the current revision without applying a move twice.
    try {
      applyGame(await api("api/game"));
      const fallback = game === null
        ? "This table has expired. You can deal a new game."
        : "The server didn’t confirm your request. The latest table is shown below; no action was retried.";
      notice(error instanceof ApiError ? error.message : fallback);
    } catch {
      online = false;
      notice("The connection dropped. Refresh the table before making another move.");
    }
  } finally {
    busy = false;
    render();
    if (!$("error").hidden) $("retry").focus();
    else if (game?.over) $("outcome-title").focus();
    else if (game && oldId !== game.id) $("table-title").focus({ preventScroll: true });
    else if (game && narrowTable.matches && $("mobile-clue-panel").open && body.action?.type === "hint") {
      const choice = Array.from($("clue-choices").querySelectorAll("button")).find((button) =>
        button.dataset.kind === body.action.kind && button.dataset.value === String(body.action.value));
      (choice || $("mobile-clue-panel").querySelector("summary")).focus({ preventScroll: true });
    }
    else if (game) $("hand").querySelector("button:not(:disabled)")?.focus({ preventScroll: true });
  }
}

function submit(action) {
  if (!game || !action) return;
  void write("api/action", { id: game.id, turn: game.turn, action });
}

function newDeal() {
  const options = recordingEnabled ? { record: saveReplay.checked } : {};
  if (recordingEnabled) {
    try {
      localStorage.setItem("hanabi.save-replays", saveReplay.checked ? "on" : "off");
    } catch {
      // Keep the choice in memory for this page.
    }
  }
  void write("api/new", options);
}

$("deal").addEventListener("click", newDeal);
$("restart").addEventListener("click", () => {
  $("restart-actions").before(recordingSettings);
  $("restart-dialog").showModal();
});
$("restart-dialog").addEventListener("close", () => $("deal").before(recordingSettings));
$("confirm-restart").addEventListener("click", () => {
  $("restart-dialog").close();
  newDeal();
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

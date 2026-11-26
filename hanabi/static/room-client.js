const $ = (id) => document.getElementById(id);
const lastTableKey = "hanabi.last-table";
const validCode = (code) => /^[A-Z0-9]{8}$/.test(code);

function roomUrl(code) {
  const url = new URL("./", window.location.href);
  url.searchParams.set("room", code);
  return url;
}

function roomPath(code) {
  return `api/rooms/${encodeURIComponent(code)}`;
}

export class RoomClient {
  constructor({ api, notice, setBusy, show }) {
    this.api = api;
    this.notice = notice;
    this.setBusy = setBusy;
    this.show = show;
    this.snapshot = null;
    this.code = null;
    this.socket = null;
    this.retryTimer = null;
    this.retryDelay = 500;
    this.pendingWrite = false;
    this.reading = false;
    this.readAgain = false;
  }

  async start() {
    $("intro").textContent = "You see your teammate’s cards. They see yours. Trade clues to build five fireworks together.";
    $("room-options").hidden = false;
    $("create-room").addEventListener("click", () => this.create("online"));
    $("join-room").addEventListener("submit", (event) => {
      event.preventDefault();
      const code = $("room-code").value.trim().toUpperCase();
      if (validCode(code)) void this.write(roomPath(code) + "/join", {});
    });
    $("copy-invite").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText($("invite-link").value);
        $("copy-result").textContent = "Invite link copied.";
      } catch {
        $("invite-link").select();
        $("copy-result").textContent = "Select Copy to copy the highlighted link.";
      }
    });
    window.addEventListener("pageshow", () => {
      if (this.snapshot) {
        this.connect();
        void this.refresh();
      }
    });
    window.addEventListener("pagehide", () => this.disconnect());
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && this.snapshot) {
        this.connect();
        void this.refresh();
      }
    });
    try {
      const last = localStorage.getItem(lastTableKey);
      if (last && validCode(last)) {
        $("resume-room").href = roomUrl(last);
        $("resume-room").hidden = false;
      }
    } catch {
      // A bookmarked room URL also restores the seat.
    }
    const invited = new URL(location.href).searchParams.get("room")?.trim().toUpperCase();
    if (invited && validCode(invited)) {
      this.code = invited;
      $("room-code").value = invited;
      await this.refresh(true);
    } else {
      this.show(null);
      if (invited) this.notice("Enter the eight-character room code from your invite.");
    }
  }

  create(mode) {
    return this.write("api/rooms", { mode });
  }

  move(body) {
    if (this.snapshot?.status === "playing") return this.write(roomPath(this.code) + "/move", body);
  }

  apply(snapshot) {
    if (!validCode(snapshot.code)) throw new Error("Invalid room response");
    const old = this.snapshot;
    if (old && old.code === snapshot.code && old.status === snapshot.status &&
        old.game?.id === snapshot.game?.id && old.game?.turn >= snapshot.game?.turn) return;
    if (old?.code === snapshot.code && old.game && !snapshot.game) return;
    this.code = snapshot.code;
    this.snapshot = snapshot;
    const url = roomUrl(this.code);
    history.replaceState(null, "", url);
    try {
      localStorage.setItem(lastTableKey, this.code);
    } catch {
      // Refresh and bookmarks use the room URL directly.
    }
    if (snapshot.status === "waiting") {
      $("invite-code").textContent = this.code;
      $("invite-link").value = url.href;
    }
    this.show(snapshot);
    if (snapshot.mode === "online" && snapshot.status !== "finished") this.connect();
    else this.disconnect();
  }

  async refresh(preview = false) {
    if (!this.code) {
      this.show(null);
      this.notice();
      return;
    }
    if (this.pendingWrite || this.reading) {
      this.readAgain = true;
      return;
    }
    this.reading = true;
    const code = this.code;
    try {
      const snapshot = await this.api(roomPath(code));
      if (code === this.code) {
        this.apply(snapshot);
        if (!this.pendingWrite) this.notice();
      }
    } catch (error) {
      if (code !== this.code) return;
      if (!this.snapshot || error.status === 403 || error.status === 401 || error.status === 404 || error.status === 410) {
        this.disconnect();
        this.snapshot = null;
        this.show(null);
        if (preview && (error.status === 403 || error.status === 401)) {
          this.notice();
          $("intro").textContent = "A teammate invited you to a game of Hanabi. Join the room to take your seat.";
          $("join-seat").focus({ preventScroll: true });
          return;
        }
      }
      this.notice(error.status ? error.message : "Couldn’t reach the room. Refresh to try again.");
    } finally {
      this.reading = false;
      this.flushRefresh();
    }
  }

  flushRefresh() {
    if (this.readAgain && !this.pendingWrite && !this.reading) {
      this.readAgain = false;
      void this.refresh();
    }
  }

  async write(path, body) {
    if (this.pendingWrite) return;
    this.pendingWrite = true;
    this.setBusy(true);
    this.notice();
    try {
      const snapshot = await this.api(path, body);
      this.apply(snapshot);
    } catch (error) {
      if (this.snapshot && path.endsWith("/move")) {
        // A lost response can follow a committed move. Read the latest turn.
        try { this.apply(await this.api(roomPath(this.code))); }
        catch { /* The refresh button retries the read. */ }
      }
      this.notice(error.status ? error.message : "The room didn’t confirm your request. Refresh to check the table.");
    } finally {
      this.pendingWrite = false;
      this.setBusy(false);
      this.flushRefresh();
    }
  }

  connectionStatus(message) {
    $("connection-status").hidden = !message;
    $("connection-status").textContent = message;
  }

  disconnect() {
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.connectionStatus("");
  }

  connect() {
    if (this.socket || this.retryTimer !== null || this.snapshot?.mode !== "online" ||
        this.snapshot.status === "finished") return;
    const url = new URL(roomPath(this.code) + "/socket", location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      if (socket !== this.socket) return;
      this.retryDelay = 500;
      this.connectionStatus("");
      void this.refresh();
    };
    socket.onmessage = (event) => {
      if (socket !== this.socket) return;
      try {
        if (JSON.parse(event.data).type === "changed") void this.refresh();
      } catch {
        socket.close();
      }
    };
    socket.onclose = () => {
      if (socket !== this.socket) return;
      this.socket = null;
      this.connectionStatus("Reconnecting to your room…");
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        this.connect();
        void this.refresh();
      }, this.retryDelay);
      this.retryDelay = Math.min(this.retryDelay * 2, 10000);
    };
  }
}

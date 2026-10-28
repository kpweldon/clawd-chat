# Clawd Chat - a message-app front end for Claude Code.
# Reads the session transcripts (~/.claude/projects/<project>/<session>.jsonl),
# boils each one down to the words: your messages, Claude's replies, a
# "ran N steps" chip where tools ran, and a card per sub-agent. Tool calls,
# code and hook payloads are never sent to the page.
# Sending a message runs `claude -p --resume <session>` headless in the
# session's own folder, so the reply lands in the same transcript.
#
#   python3 server.py            serve on 127.0.0.1:8792
#
# Exits by itself once the window has been gone IDLE_EXIT_S and nothing is running.
import os, re, json, time, uuid, glob, threading, subprocess, signal
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTS = os.path.expanduser("~/.claude/projects")
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
PORT = CONFIG.get("port", 8792)
DAYS = CONFIG.get("days", 14)
IDLE_EXIT_S = CONFIG.get("idle_exit_minutes", 15) * 60
CLAUDE = os.path.expanduser(CONFIG.get("claude", "~/.local/bin/claude"))
JOBS_DIR = os.path.join(HERE, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

ENV = dict(os.environ)
ENV["CLAUDE_CHAT_CHILD"] = "1"      # autoopen.sh: don't pop the window for our own replies
ENV["PATH"] = ":".join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
                        "/usr/bin", "/bin", "/usr/sbin", "/sbin", ENV.get("PATH", "")])

CHAT_NOTE = CONFIG.get("chat_note") or (
    "The user is messaging you from Clawd Chat, a message-style window that shows only your "
    "plain-text replies (tool calls and code are hidden from them). Skip any session-start menu "
    "or greeting. Answer like a text message: result first, short, no code dumps, no long file "
    "listings. If you need them to pick or approve something, ask in plain words; they reply in "
    "the next message.")

TOOL_AGENT = ("Agent", "Task")


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _user_text(o):
    """What the user typed in a `user` record, or '' for tool results / hook payloads."""
    if o.get("isMeta") or o.get("isCompactSummary"):
        return ""
    c = o.get("message", {}).get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return ""
        t = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    else:
        return ""
    t = t.strip()
    if not t or t.startswith("[Request interrupted") or t.startswith("Base directory for this skill"):
        return ""
    if t.startswith("<"):
        # slash command -> "/name args"; every other tagged payload is machinery
        m = re.search(r"<command-name>(.*?)</command-name>", t, re.S)
        if not m:
            return ""
        a = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
        name = m.group(1).strip()
        return (name if name.startswith("/") else "/" + name) + (" " + a.group(1).strip() if a and a.group(1).strip() else "")
    return t


class Transcript:
    """Incremental parse of one .jsonl: feed() only reads bytes appended since last time."""

    def __init__(self, path, sidechain=False):
        self.path, self.sidechain = path, sidechain
        self.offset, self.buf = 0, b""
        self.items, self.seen = [], set()
        self.title = self.custom = ""
        self.cwds = []
        self.first = self.last = 0
        self.pending_tools = 0
        self.user_texts = set()
        self.last_kind = ""
        self.lock = threading.Lock()

    def feed(self):
        with self.lock:
            try:
                size = os.path.getsize(self.path)
            except OSError:
                return
            if size < self.offset:            # rewritten - start over
                self.__init__(self.path, self.sidechain)
            if size == self.offset:
                return
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = self.buf + f.read(size - self.offset)
            self.offset = size
            lines = data.split(b"\n")
            self.buf = lines.pop()               # partial last line
            for line in lines:
                head = line[:120]
                if b'"api-request' in head or b'"file-history' in head or b'"attachment"' in head:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                self._record(o)

    def _add(self, item):
        if self.pending_tools and item["kind"] in ("claude", "user"):
            self.items.append({"kind": "work", "n": self.pending_tools, "ts": item.get("ts", 0)})
            self.pending_tools = 0
        self.items.append(item)
        self.last_kind = item["kind"]

    def _record(self, o):
        t = o.get("type")
        if t == "ai-title":
            self.title = o.get("aiTitle") or self.title
            return
        if t == "custom-title":
            self.custom = o.get("customTitle") or self.custom
            return
        ts = _ts(o.get("timestamp", ""))
        if ts:
            self.first = self.first or ts
            self.last = max(self.last, ts)
        cwd = o.get("cwd")
        if cwd and (not self.cwds or self.cwds[-1] != cwd):
            if cwd not in self.cwds:
                self.cwds.append(cwd)
        if t == "queue-operation" and o.get("operation") == "enqueue":
            txt = (o.get("content") or "").strip()
            if txt and not txt.startswith("<"):
                self.user_texts.add(txt)
                self._add({"kind": "user", "text": txt, "ts": ts, "queued": True})
            return
        if t not in ("user", "assistant"):
            return
        if o.get("isSidechain") and not self.sidechain:
            return
        uid = o.get("uuid")
        if uid:
            if uid in self.seen:
                return
            self.seen.add(uid)
        if t == "user":
            txt = _user_text(o)
            if txt and txt not in self.user_texts:
                self.user_texts.add(txt)
                self._add({"kind": "user", "text": txt, "ts": ts})
            return
        c = o.get("message", {}).get("content")
        if isinstance(c, str):
            c = [{"type": "text", "text": c}]
        for b in c or []:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and b.get("text", "").strip():
                txt = b["text"].strip()
                if self.last_kind == "claude" and not self.pending_tools:
                    self.items[-1]["text"] += "\n\n" + txt
                    self.items[-1]["ts"] = ts
                else:
                    self._add({"kind": "claude", "text": txt, "ts": ts})
            elif bt == "tool_use":
                if b.get("name") in TOOL_AGENT:
                    inp = b.get("input") or {}
                    if self.pending_tools:
                        self.items.append({"kind": "work", "n": self.pending_tools, "ts": ts})
                        self.pending_tools = 0
                    self.items.append({"kind": "agent", "tool_use_id": b.get("id"), "ts": ts,
                                       "description": inp.get("description") or "Sub-agent",
                                       "type": inp.get("subagent_type") or "general-purpose"})
                    self.last_kind = "agent"
                else:
                    self.pending_tools += 1

    # -- views -----------------------------------------------------------
    def first_prompt(self):
        for it in self.items:
            if it["kind"] == "user":
                return it["text"]
        return ""

    def display_title(self):
        t = self.custom or self.title or self.first_prompt()
        return re.sub(r"\s+", " ", t).strip()[:90] or "Untitled"

    def preview(self):
        for it in reversed(self.items):
            if it["kind"] in ("claude", "user"):
                txt = re.sub(r"^#\s*\*\*CLAUDE\*\*[^\n]*\n-{3,}\s*", "", it["text"])
                txt = re.sub(r"[#*`>|_-]{1,}", " ", txt)
                return ("You: " if it["kind"] == "user" else "") + re.sub(r"\s+", " ", txt).strip()[:140]
        return ""


CACHE, CACHE_LOCK = {}, threading.Lock()


def transcript(path, sidechain=False):
    with CACHE_LOCK:
        tr = CACHE.get(path)
        if tr is None:
            tr = CACHE[path] = Transcript(path, sidechain)
    tr.feed()
    return tr


def session_path(sid):
    if not re.fullmatch(r"[0-9a-f-]{36}", sid or ""):
        return None
    hits = glob.glob(os.path.join(PROJECTS, "*", sid + ".jsonl"))
    return hits[0] if hits else None


def project_cwd(path, tr):
    """The folder the session was started in (claude --resume looks the
    session up under that folder's project directory)."""
    proj = os.path.basename(os.path.dirname(path))
    for c in tr.cwds:
        if re.sub(r"[^A-Za-z0-9]", "-", c) == proj:
            return c
    return tr.cwds[0] if tr.cwds else os.path.expanduser("~")


def agents_of(path):
    """toolUseId -> {agent_id, finished} for a session's sub-agents."""
    out = {}
    sdir = os.path.join(os.path.splitext(path)[0], "subagents")
    for m in glob.glob(os.path.join(sdir, "agent-*.meta.json")):
        try:
            meta = json.load(open(m))
        except Exception:
            continue
        aid = os.path.basename(m)[len("agent-"):-len(".meta.json")]
        jp = os.path.join(sdir, "agent-%s.jsonl" % aid)
        try:
            mt = os.path.getmtime(jp)
        except OSError:
            mt = 0
        out[meta.get("toolUseId")] = {"agent_id": aid, "finished": bool(meta.get("finished")),
                                      "mtime": mt, "description": meta.get("description", ""),
                                      "type": meta.get("agentType", "")}
    return out


# -- labels: your own name, colour, pin per thread (meta.json) -------------
META_PATH = os.path.join(HERE, "meta.json")
META_LOCK = threading.Lock()
COLORS = ("orange", "red", "amber", "green", "teal", "blue", "purple", "pink", "gray", "")


def load_meta():
    try:
        return json.load(open(META_PATH))
    except Exception:
        return {}


def save_meta(sid, body):
    if not re.fullmatch(r"[0-9a-f-]{36}", sid or ""):
        raise ValueError("bad thread id")
    with META_LOCK:
        meta = load_meta()
        m = meta.get(sid, {})
        if "title" in body:
            t = re.sub(r"\s+", " ", str(body["title"] or "")).strip()[:80]
            if t:
                m["title"] = t
            else:
                m.pop("title", None)
        if "color" in body:
            c = str(body["color"] or "")
            if c not in COLORS:
                raise ValueError("unknown colour")
            if c:
                m["color"] = c
            else:
                m.pop("color", None)
        if "pinned" in body:
            if body["pinned"]:
                m["pinned"] = time.time()
            else:
                m.pop("pinned", None)
        if m:
            meta[sid] = m
        else:
            meta.pop(sid, None)
        tmp = META_PATH + ".tmp"
        json.dump(meta, open(tmp, "w"), indent=1)
        os.replace(tmp, META_PATH)
        return m


# -- jobs (messages sent from the app) -------------------------------------
JOBS, JOBS_LOCK = {}, threading.Lock()   # sid -> {"proc", "started", "log"}


def recently_ours(sid, mt):
    """True if the transcript's last write came from a job this app ran."""
    with JOBS_LOCK:
        j = JOBS.get(sid)
    if not j:
        return False
    try:
        return mt <= os.path.getmtime(j["log"]) + 5
    except OSError:
        return False


_PS = {"t": 0, "out": ""}


def headless_pids():
    """`claude -p` processes alive right now (cached 2 s). Catches replies the
    app started before a server restart, which JOBS no longer knows about."""
    if time.time() - _PS["t"] > 2:
        try:
            _PS["out"] = subprocess.run(["ps", "-axo", "pid=,lstart=,args="], capture_output=True, text=True).stdout
        except Exception:
            _PS["out"] = ""
        _PS["t"] = time.time()
    return _PS["out"]


_TS = {"t": 0, "map": {}}


def terminal_sessions():
    """{session id: "busy"|"idle"} for Claude Code sessions open in a terminal right now,
    from the pid files Claude Code keeps in ~/.claude/sessions (cached 2 s).
    Headless runs (the app's own replies) say entrypoint "sdk-cli" and are left out."""
    if time.time() - _TS["t"] > 2:
        live = {}
        for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
            try:
                d = json.load(open(f))
                if d.get("entrypoint") != "cli" or not d.get("sessionId"):
                    continue
                os.kill(int(d["pid"]), 0)          # raises if that claude has exited
            except Exception:
                continue
            live[d["sessionId"]] = d.get("status") or "idle"
        _TS["map"], _TS["t"] = live, time.time()
    return _TS["map"]


def running(sid):
    with JOBS_LOCK:
        j = JOBS.get(sid)
        if j and j["proc"].poll() is None:
            return j
    for line in headless_pids().splitlines():
        if " -p " in line and "--append-system-prompt" in line and \
                ("--resume " + sid in line or "--session-id " + sid in line):
            parts = line.split()
            try:
                started = datetime.strptime(" ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y").timestamp()
            except Exception:
                started = time.time()
            return {"proc": None, "started": started, "orphan": True}
    return None


def send(sid, text, cwd=None):
    new = not sid
    if new:
        sid = str(uuid.uuid4())
        cwd = os.path.expanduser(cwd or CONFIG.get("new_chat_folder", "~"))
        args = ["--session-id", sid]
    else:
        path = session_path(sid)
        if not path:
            raise ValueError("session not found")
        if running(sid):
            raise ValueError("Claude is still answering the last message in this thread")
        cwd = project_cwd(path, transcript(path))
        args = ["--resume", sid]
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    cmd = [CLAUDE, "-p", *args, "--permission-mode", CONFIG.get("permission_mode", "auto"),
           "--output-format", "stream-json", "--verbose", "--append-system-prompt", CHAT_NOTE]
    log = open(os.path.join(JOBS_DIR, "%s-%d.log" % (sid, time.time())), "w")
    proc = subprocess.Popen(cmd, cwd=cwd, env=ENV, stdin=subprocess.PIPE, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)
    proc.stdin.write(text.encode())
    proc.stdin.close()
    with JOBS_LOCK:
        JOBS[sid] = {"proc": proc, "started": time.time(), "log": log.name, "text": text, "cwd": cwd}
    return sid


def job_error(sid):
    """Last failed run's error text, if the most recent job for sid exited non-zero."""
    with JOBS_LOCK:
        j = JOBS.get(sid)
    if not j or j["proc"].poll() in (None, 0):
        return ""
    try:
        tail = open(j["log"]).read()[-4000:]
    except Exception:
        tail = ""
    for line in reversed(tail.splitlines()):
        try:
            o = json.loads(line)
            if o.get("type") == "result":
                return str(o.get("result") or o.get("subtype") or "failed")[:500]
        except Exception:
            if line.strip():
                return line.strip()[:500]
    return "Claude exited with code %s" % j["proc"].returncode


def stop(sid):
    j = running(sid)
    if j and j.get("orphan"):
        for line in headless_pids().splitlines():
            if " -p " in line and sid in line and "--append-system-prompt" in line:
                try:
                    os.kill(int(line.split()[0]), signal.SIGINT)
                except Exception:
                    pass
        _PS["t"] = 0
        return True
    if j:
        try:
            os.killpg(j["proc"].pid, signal.SIGINT)
        except Exception:
            j["proc"].terminate()
        return True
    return False


# -- api ------------------------------------------------------------------
def api_threads():
    cutoff = time.time() - DAYS * 86400
    meta = load_meta()
    out = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        sid = os.path.basename(path)[:-6]
        m = meta.get(sid, {})
        if mt < cutoff and not m.get("pinned"):
            continue
        tr = transcript(path)
        if not any(it["kind"] == "user" for it in tr.items):
            continue
        ag = agents_of(path)
        job = running(sid)
        out.append({
            "id": sid, "title": m.get("title") or tr.display_title(), "auto_title": tr.display_title(),
            "color": m.get("color", ""), "pinned": m.get("pinned", 0), "preview": tr.preview(),
            "cwd": project_cwd(path, tr), "updated": max(tr.last, mt if not tr.last else 0) or mt,
            "messages": sum(1 for it in tr.items if it["kind"] in ("user", "claude")),
            "agents": len(ag), "agents_running": sum(1 for a in ag.values() if not a["finished"] and time.time() - a["mtime"] < 300),
            "working": bool(job), "live": sid in terminal_sessions(), "live_status": terminal_sessions().get(sid, ""),
            "waiting": tr.items[-1]["kind"] == "claude" if tr.items else False,
        })
    out.sort(key=lambda x: -x["updated"])
    return {"threads": out, "now": time.time()}


def api_thread(sid):
    path = session_path(sid)
    if not path:
        return {"error": "not found"}
    tr = transcript(path)
    ag = agents_of(path)
    items = []
    for it in tr.items:
        it = dict(it)
        if it["kind"] == "agent":
            a = ag.get(it.get("tool_use_id")) or {}
            it["agent_id"] = a.get("agent_id")
            it["finished"] = a.get("finished", True)
            it["active"] = bool(a) and not a.get("finished") and time.time() - a.get("mtime", 0) < 300
        items.append(it)
    job = running(sid)
    mt = os.path.getmtime(path)
    m = load_meta().get(sid, {})
    return {"id": sid, "title": m.get("title") or tr.display_title(), "auto_title": tr.display_title(),
            "color": m.get("color", ""), "pinned": m.get("pinned", 0),
            "cwd": project_cwd(path, tr), "items": items,
            "working": bool(job), "working_since": job["started"] if job else 0,
            "pending_steps": tr.pending_tools, "live": sid in terminal_sessions(), "live_status": terminal_sessions().get(sid, ""),
            "error": "" if job else job_error(sid), "version": tr.offset}


def api_agent(sid, aid):
    path = session_path(sid)
    if not path or not re.fullmatch(r"[0-9a-f]+", aid or ""):
        return {"error": "not found"}
    jp = os.path.join(os.path.splitext(path)[0], "subagents", "agent-%s.jsonl" % aid)
    if not os.path.exists(jp):
        return {"error": "not found"}
    tr = transcript(jp, sidechain=True)
    meta = {}
    try:
        meta = json.load(open(jp[:-6] + ".meta.json"))
    except Exception:
        pass
    return {"items": tr.items, "description": meta.get("description", ""), "type": meta.get("agentType", ""),
            "finished": bool(meta.get("finished")), "pending_steps": tr.pending_tools,
            "active": time.time() - os.path.getmtime(jp) < 300 and not meta.get("finished")}


LAST_ALIVE = [time.time()]
PAGE_ALIVE = [time.time()]          # last heartbeat from the chat page itself
CHAT_PROFILE = os.path.expanduser("~/Library/Application Support/ClaudeChatProfile")
PAGE_GONE_S = 150                   # > Chrome's 1/min timer throttling for hidden pages


def chat_chrome_pids():
    """Main Chrome process(es) running the chat window's own profile."""
    out = subprocess.run(["ps", "-axo", "pid=,comm=,args="], capture_output=True, text=True).stdout
    return [int(l.split()[0]) for l in out.splitlines()
            if "Google Chrome.app/Contents/MacOS/Google Chrome" in l and "--user-data-dir=" + CHAT_PROFILE in l]


def chrome_watch():
    """Quit the chat's Chrome once its window is gone. macOS Chrome stays alive with
    no windows, and anything that later activates it (Dock, launcher) makes it open
    a blank New Tab window."""
    while True:
        time.sleep(10)
        if time.time() - PAGE_ALIVE[0] < PAGE_GONE_S:
            continue
        for pid in chat_chrome_pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, ctype):
        try:
            body = open(os.path.join(HERE, name), "rb").read()
        except OSError:
            return self._json({"error": "missing"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        """Only answer requests addressed to us by our own name: blocks DNS-rebinding
        pages from reading transcripts through a hostname that resolves to 127.0.0.1."""
        return self.headers.get("Host") in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        LAST_ALIVE[0] = time.time()
        try:
            if u.path in ("/", "/index.html"):
                return self._file("chat.html", "text/html; charset=utf-8")
            if u.path == "/clawd.svg":
                return self._file("clawd.svg", "image/svg+xml")
            if u.path == "/ping":
                return self._json({"ok": True})
            if u.path == "/alive":
                PAGE_ALIVE[0] = time.time()
                return self._json({"ok": True})
            if u.path == "/bye":
                # window closing (or reloading: the reload's own /alive lands within seconds)
                PAGE_ALIVE[0] = time.time() - PAGE_GONE_S + 8
                return self._json({"ok": True})
            if u.path == "/api/threads":
                return self._json(api_threads())
            if u.path == "/api/thread":
                return self._json(api_thread(q.get("id", "")))
            if u.path == "/api/agent":
                return self._json(api_agent(q.get("id", ""), q.get("agent", "")))
            return self._json({"error": "no route"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        LAST_ALIVE[0] = time.time()
        if not self._host_ok() or self.headers.get("Origin") not in (None, "http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT):
            return self._json({"error": "bad origin"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            if u.path == "/bye":            # navigator.sendBeacon from the closing page
                PAGE_ALIVE[0] = time.time() - PAGE_GONE_S + 8
                return self._json({"ok": True})
            body = json.loads(raw or b"{}")
            if u.path == "/api/send":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json({"error": "empty message"}, 400)
                sid = send(body.get("id"), text, body.get("cwd"))
                return self._json({"ok": True, "id": sid})
            if u.path == "/api/meta":
                return self._json({"ok": True, "meta": save_meta(body.get("id", ""), body)})
            if u.path == "/api/stop":
                return self._json({"ok": stop(body.get("id", ""))})
            if u.path == "/api/open":
                # links and file paths in Claude's replies -> the normal browser / Finder
                target = body.get("url") or ""
                if re.match(r"https?://", target):
                    subprocess.Popen(["open", target])
                elif target.startswith("/") or target.startswith("~"):
                    p = os.path.expanduser(target)
                    if os.path.exists(p):
                        subprocess.Popen(["open", p] if not body.get("reveal") else ["open", "-R", p])
                    else:
                        return self._json({"error": "file not found"}, 404)
                return self._json({"ok": True})
            return self._json({"error": "no route"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def reaper(server):
    while True:
        time.sleep(30)
        with JOBS_LOCK:
            busy = any(j["proc"].poll() is None for j in JOBS.values())
        busy = busy or "--append-system-prompt" in headless_pids()
        if not busy and time.time() - LAST_ALIVE[0] > IDLE_EXIT_S:
            server.shutdown()
            return


def daemonize():
    """Double fork so the server outlives the Dock app's launcher script."""
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    log = open(os.path.join(HERE, "server.log"), "a")
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    devnull = open(os.devnull)
    os.dup2(devnull.fileno(), 0)


if __name__ == "__main__":
    import sys
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)   # bind first: a second copy fails here
    if "--daemon" in sys.argv:
        daemonize()
    threading.Thread(target=reaper, args=(srv,), daemon=True).start()
    threading.Thread(target=chrome_watch, daemon=True).start()
    print("Clawd Chat on http://127.0.0.1:%d" % PORT, flush=True)
    srv.serve_forever()

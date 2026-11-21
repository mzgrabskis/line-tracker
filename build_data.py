"""Builds the static data files in docs/data/.  Used by GitHub Actions and by update-nba-data.bat.
Usage: py build_data.py [nba] [el]      (no argument = both)"""
import sys, os, re, json, time, unicodedata, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests
import app

OUT = Path(__file__).with_name("docs") / "data"; OUT.mkdir(parents=True, exist_ok=True)
SCHED = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
CI = bool(os.environ.get("CI"))
today = dt.date.today()
LO, HI = (today - dt.timedelta(days=2)).isoformat(), (today + dt.timedelta(days=10)).isoformat()

def pack(rows, lg):
    players, logs = {}, []
    for g in rows:
        players.setdefault(g["pid"], [g["name"], app.photo(lg, g)])
        logs.append([g["pid"], g["team"], g["date"], 1 if g["home"] else 0, g["opp"], g["wl"], g["min"], g["pts"],
                     g["reb"], g["ast"], g["fg3m"], g["stl"], g["blk"], g["tov"]])
    return {"players": players, "logs": logs}

def save(name, d):
    d["updated"] = time.time()
    (OUT / f"{name}.json").write_text(json.dumps(d, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"{name}: wrote {len(d['logs'])} game lines, {len(d['sched'])} scheduled games", flush=True)

NOW = lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
TEAM = lambda t: {"code": t["teamTricode"], "name": f'{t.get("teamCity", "")} {t.get("teamName", "")}'.strip()}

def sched_from_file():
    for url in (SCHED, SCHED.replace("V2.json", "V2_1.json")):
        try:
            js = requests.get(url, timeout=30, headers=app.HDR).json()
            out = []
            for gd in js["leagueSchedule"]["gameDates"]:
                for g in gd["games"]:
                    d = (g.get("gameDateEst") or g.get("gameDateTimeEst") or "")[:10]
                    t = g.get("gameDateTimeUTC") or ""
                    if LO <= d <= HI and "T" in t:
                        out.append({"id": g["gameId"], "date": d, "time": t, "home": TEAM(g["homeTeam"]), "away": TEAM(g["awayTeam"])})
            if out: return out
        except Exception as e: print("  schedule file failed:", e, flush=True)
    return []

def et_to_utc(text, d):
    from zoneinfo import ZoneInfo
    m = re.search(r"(\d{1,2}):(\d{2})\s*([ap])m", text or "", re.I)
    if not m: return ""
    h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    local = dt.datetime(d.year, d.month, d.day, h, int(m.group(2)), tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def sched_from_v2():                      # same NBA.com stats host that already works for the game logs
    from nba_api.stats.endpoints import scoreboardv2
    from nba_api.stats.static import teams
    from zoneinfo import ZoneInfo
    T = {t["id"]: t for t in teams.get_teams()}
    base, out = dt.datetime.now(ZoneInfo("America/New_York")).date(), []
    for k in range(-1, 5):
        d = base + dt.timedelta(days=k)
        try:
            rows = scoreboardv2.ScoreboardV2(game_date=d.strftime("%m/%d/%Y"), timeout=30).get_data_frames()[0].to_dict("records")
        except Exception as e:
            print("  ScoreboardV2", d, "failed:", e, flush=True); continue
        for x in rows:
            h, a, t = T.get(x["HOME_TEAM_ID"]), T.get(x["VISITOR_TEAM_ID"]), et_to_utc(x.get("GAME_STATUS_TEXT"), d)
            if h and a and t:             # games without a start time are live/finished, not upcoming
                out.append({"id": str(x["GAME_ID"]), "date": d.isoformat(), "time": t,
                            "home": {"code": h["abbreviation"], "name": h["full_name"]},
                            "away": {"code": a["abbreviation"], "name": a["full_name"]}})
    return out

def sched_from_live():
    t = app.nba_today_raw()
    return [{"id": g["id"], "date": t["date"], "time": g["time"], "home": g["home"], "away": g["away"]} for g in t["games"]]

def nba_sched():
    best = []
    for name, fn in (("schedule file", sched_from_file), ("ScoreboardV2", sched_from_v2), ("live scoreboard", sched_from_live)):
        try: out = fn()
        except Exception as e: print(f"NBA schedule via {name} failed: {e}", flush=True); continue
        up = [g for g in out if g["time"] > NOW()]
        print(f"NBA schedule via {name}: {len(out)} games, {len(up)} upcoming", flush=True)
        if up: return out
        best = best or out
    return best

def build_nba_official():
    rows, err = [], None
    for off in (0, 1):                           # this season + previous season (for head-to-head)
        for _ in range(1 if CI else 2):
            try: rows += app.nba_logs_raw(off); err = None; break
            except Exception as e: err = e; time.sleep(2)
        if err:
            if off == 0: raise err
            print("previous NBA season skipped:", err, flush=True)
    d = pack(rows, "nba"); d["sched"] = nba_sched(); save("nba", d)

# ---------------- NBA from ESPN's public feed (works from GitHub's servers) ----------------
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_FIX = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}   # NBA.com-style codes

def espn_get(path, params=None):
    err = None
    for i in range(3):
        try:
            r = requests.get(ESPN + path, params=params, headers=app.HDR, timeout=30)
            r.raise_for_status(); return r.json()
        except Exception as e: err = e; time.sleep(1 + i)
    raise err

def utc_iso(s): return s.replace("Z", "")[:16] + ":00Z"

def et_date(s):
    from zoneinfo import ZoneInfo
    t = dt.datetime.strptime(s.replace("Z", "")[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ZoneInfo("America/New_York")).date().isoformat()

def fnum(x):
    x = str(x).strip()
    try:
        if ":" in x: m, sec = x.split(":")[:2]; return float(m) + float(sec) / 60
        return float(x.split("-")[0] or 0)          # "3-7" (made-attempted) -> 3 ; "--" -> 0
    except Exception: return 0.0

def nname(n):
    n = "".join(c for c in unicodedata.normalize("NFKD", n.lower()) if not unicodedata.combining(c))
    return " ".join(w for w in re.sub(r"[^a-z0-9 ]", " ", n).split() if w not in ("jr", "sr", "ii", "iii", "iv"))

def photo_map():                                     # name -> NBA.com photo (offline list shipped with nba_api)
    from nba_api.stats.static import players as P
    m = {}
    for p in sorted(P.get_players(), key=lambda p: p["is_active"]):      # active players are written last, so they win
        m[nname(p["full_name"])] = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{p['id']}.png"
    return m

def espn_event(ev):
    if (ev.get("season") or {}).get("type") == 1: return None            # preseason
    comp, side = (ev.get("competitions") or [{}])[0], {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}; ab = t.get("abbreviation", "")
        side[c.get("homeAway")] = {"id": str(t.get("id")), "code": ESPN_FIX.get(ab, ab), "name": t.get("displayName") or ab, "win": c.get("winner")}
    if "home" not in side or "away" not in side: return None
    st = ((ev.get("status") or comp.get("status") or {}).get("type")) or {}
    return {"id": str(ev["id"]), "time": utc_iso(ev["date"]), "date": et_date(ev["date"]), "state": st.get("state", ""),
            "completed": bool(st.get("completed")), "home": side["home"], "away": side["away"]}

def espn_lines(js, ev, season, names):
    out = []
    for blk in (js.get("boxscore") or {}).get("players") or []:
        tid = str((blk.get("team") or {}).get("id"))
        if tid == ev["home"]["id"]: me, opp, home = ev["home"], ev["away"], True
        elif tid == ev["away"]["id"]: me, opp, home = ev["away"], ev["home"], False
        else: continue
        wl = "" if me.get("win") is None else ("W" if me["win"] else "L")
        for grp in blk.get("statistics") or []:
            lab = [str(x).upper() for x in (grp.get("labels") or grp.get("names") or [])]
            ix = lambda *n: next((lab.index(k) for k in n if k in lab), None)
            iM, iP, iT, iR, iA, iS, iB, iO = ix("MIN"), ix("PTS"), ix("3PT"), ix("REB"), ix("AST"), ix("STL"), ix("BLK"), ix("TO", "TOV")
            if iM is None or iP is None: continue
            for a in grp.get("athletes") or []:
                st = a.get("stats") or []
                v = lambda i: fnum(st[i]) if i is not None and i < len(st) else 0
                if a.get("didNotPlay") or not st or v(iM) <= 0: continue
                ath = a.get("athlete") or {}; pid, name = str(ath.get("id")), ath.get("displayName") or ath.get("shortName") or ""
                pic = (ath.get("headshot") or {}).get("href") or names.get(nname(name)) or f"https://a.espncdn.com/i/headshots/nba/players/full/{pid}.png"
                out.append(app.mk(pid, name, me["code"], ev["date"], home, opp["code"], wl, v(iM), v(iP), v(iR), v(iA), v(iT), v(iS), v(iB), v(iO), season, pic))
    return out

def build_nba_espn():
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    y = today.year if today.month >= 10 else today.year - 1
    season, f, dbg = f"{y}-{str(y + 1)[2:]}", app.CACHE / f"espn_nba_{y}.json", []
    try: st = json.loads(f.read_text(encoding="utf-8"))
    except Exception: st = {"done": [], "events": {}}
    events, done = st["events"], set(st["done"])
    days, d = [], dt.date(y, 10, 1)
    while d <= today + dt.timedelta(days=7): days.append(d); d += dt.timedelta(days=1)
    todo = [d for d in days if d.isoformat() not in done]

    def sb(d):
        try: return d, espn_get("/scoreboard", {"dates": d.strftime("%Y%m%d"), "limit": 100}).get("events") or []
        except Exception as e: dbg.append(f"scoreboard {d} failed: {e}"); return d, None
    with ThreadPoolExecutor(6) as ex: results = list(ex.map(sb, todo))
    if todo and all(r[1] is None for r in results): raise RuntimeError("ESPN scoreboard unreachable: " + (dbg[0] if dbg else ""))
    sched = []
    for d, evs in results:
        if evs is None: continue
        all_final = True
        for raw in evs:
            ev = espn_event(raw)
            if not ev: continue
            if ev["completed"] or ev["state"] == "post": events.setdefault(ev["id"], {**ev, "lines": None})
            else:
                all_final = False
                if ev["state"] == "pre": sched.append({"id": ev["id"], "date": ev["date"], "time": ev["time"], "home": {"code": ev["home"]["code"], "name": ev["home"]["name"]}, "away": {"code": ev["away"]["code"], "name": ev["away"]["name"]}})
        if d <= today - dt.timedelta(days=2) and all_final: done.add(d.isoformat())

    names, pend = photo_map(), [e for e in events.values() if e["lines"] is None]
    if pend: print(f"ESPN NBA: downloading {len(pend)} box score(s)...", flush=True)
    def box(e):
        try:
            js = espn_get("/summary", {"event": e["id"]}); lines = espn_lines(js, e, season, names)
            if not lines and len(dbg) < 8:
                bs = js.get("boxscore") or {}
                dbg.append(f"event {e['id']}: no players parsed. top keys={list(js)[:12]} boxscore keys={list(bs)} blocks={len(bs.get('players') or [])}")
            return e, lines
        except Exception as ex_: dbg.append(f"summary {e['id']} failed: {ex_}"); return e, None
    cutoff = (today - dt.timedelta(days=3)).isoformat()
    with ThreadPoolExecutor(6) as ex:
        for e, lines in ex.map(box, pend):
            if lines is not None and (lines or e["date"] < cutoff): e["lines"] = lines
    f.write_text(json.dumps({"done": sorted(done), "events": events}), encoding="utf-8")

    rows = [l for e in events.values() for l in (e["lines"] or [])]
    (OUT / "nba_debug.txt").write_text("source: ESPN\n" + f"games with stats: {sum(1 for e in events.values() if e['lines'])}\nplayer lines: {len(rows)}\nupcoming games: {len(sched)}\n" + "\n".join(dbg[:30]), encoding="utf-8")
    if not rows: raise RuntimeError("ESPN returned no player stats. " + " | ".join(dbg[:3]))
    d = pack(rows, "espn"); d["sched"] = sorted(sched, key=lambda g: g["time"]); save("nba", d)

def build_nba():
    try: build_nba_espn(); return
    except Exception as e:
        print(f"ESPN NBA build failed: {type(e).__name__}: {e}", flush=True)
        (OUT / "nba_debug.txt").write_text(f"source: ESPN FAILED\n{type(e).__name__}: {e}\n", encoding="utf-8")
        if CI: raise                                  # NBA.com blocks GitHub's servers, so no point trying it there
    print("Trying NBA.com instead...", flush=True); build_nba_official()

def build_el():
    st, _ = app.el_update(force=True)
    rows = [r for ls in st["lines"].values() for r in ls]
    d = pack(rows, "el")
    d["sched"] = [{"id": g["code"], "date": g["date"], "time": g["time"], "home": g["home"], "away": g["away"]}
                  for g in st["sched"] if LO <= g["date"] <= HI]
    save("el", d)

if __name__ == "__main__":
    targets = sys.argv[1:] or ["nba", "el"]; failed = 0
    for t in targets:
        try: {"nba": build_nba, "nbacom": build_nba_official, "el": build_el}[t]()
        except Exception as e: failed += 1; print(f"{t.upper()} FAILED (old data kept): {type(e).__name__}: {e}", flush=True)
    (OUT / "meta.json").write_text(json.dumps({"updated": time.time()}), encoding="utf-8")
    sys.exit(1 if failed == len(targets) else 0)

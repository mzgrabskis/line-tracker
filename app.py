"""Local NBA + EuroLeague line tracker.  Run: py app.py  ->  http://localhost:5000"""
import json, re, time, datetime as dt, threading, unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests
from flask import Flask, jsonify, request, render_template
from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import playergamelog, leaguegamelog
from nba_api.live.nba.endpoints import scoreboard

app = Flask(__name__)
CACHE = Path(__file__).with_name("cache"); CACHE.mkdir(exist_ok=True)
TTL = 15 * 60
STATS = ["pts", "reb", "ast", "fg3m"]
MIN_MEAN = {"pts": 8, "reb": 3, "ast": 2.5, "fg3m": 1.5}   # ignore trivial lines
EL = "https://api-live.euroleague.net"
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ---------- helpers ----------
def num(x):
    try: return float(x or 0)
    except Exception: return 0.0

def pdate(s):
    s = str(s or "")
    if re.match(r"\d{4}-\d{2}-\d{2}", s): return s[:10]
    for f in ("%b %d, %Y", "%B %d, %Y"):
        try: return dt.datetime.strptime(s.title(), f).date().isoformat()
        except ValueError: pass
    return ""

def mk(pid, name, team, date, home, opp, wl, mn, pts, reb, ast, fg3m, stl, blk, tov, season="", photo=""):
    i = lambda v: int(num(v))
    return {"pid": str(pid), "name": name, "team": team, "date": date, "home": home, "opp": opp, "wl": wl or "",
            "min": round(num(mn), 1), "pts": i(pts), "reb": i(reb), "ast": i(ast), "fg3m": i(fg3m),
            "stl": i(stl), "blk": i(blk), "tov": i(tov), "season": season, "photo": photo,
            "matchup": f"{team} {'vs.' if home else '@'} {opp}"}

def cached(key, fn, ttl=TTL, force=False):
    f = CACHE / f"{key}.json"; old = None
    try: old = json.loads(f.read_text(encoding="utf-8"))
    except Exception: pass
    if old and not force and time.time() - old["t"] < ttl: return old["d"], old["t"], False
    try: d = fn()
    except Exception:
        if old: return old["d"], old["t"], True      # stale data beats nothing
        raise
    t = time.time()
    try: f.write_text(json.dumps({"t": t, "d": d}), encoding="utf-8")
    except Exception: pass
    return d, t, False

def photo(lg, g):
    return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{g['pid']}.png" if lg == "nba" else g.get("photo", "")

def by_player(logs):
    d = {}
    for g in logs: d.setdefault(g["pid"], []).append(g)    # logs are sorted newest first
    return d

def recent(g):
    try: return (dt.date.today() - dt.date.fromisoformat(g["date"])).days <= 12
    except Exception: return False

def norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))

# ---------- NBA ----------
def nba_season(off=0):
    t = dt.date.today(); y = (t.year if t.month >= 10 else t.year - 1) - off
    return f"{y}-{str(y + 1)[2:]}"

def nba_player_games(pid):
    rows = []
    for off in range(3):                      # current + 2 previous seasons (for head-to-head)
        s, err = nba_season(off), None
        for _ in range(2):
            try:
                df = playergamelog.PlayerGameLog(player_id=pid, season=s, timeout=30).get_data_frames()[0]
                for r in df.to_dict("records"):
                    m = r["MATCHUP"]
                    rows.append(mk(pid, "", m.split()[0], pdate(r["GAME_DATE"]), "vs." in m, m.split()[-1], r["WL"],
                                   r["MIN"], r["PTS"], r["REB"], r["AST"], r["FG3M"], r["STL"], r["BLK"], r["TOV"], s))
                err = None; break
            except Exception as e:
                err = e; time.sleep(1.5)
        if err and not rows: raise err
    rows.sort(key=lambda g: g["date"], reverse=True)
    return rows

def nba_logs_raw(off=0):
    df = leaguegamelog.LeagueGameLog(season=nba_season(off), player_or_team_abbreviation="P", timeout=60).get_data_frames()[0]
    rows = []
    for r in df.to_dict("records"):
        m = r["MATCHUP"]
        rows.append(mk(r["PLAYER_ID"], r["PLAYER_NAME"], r["TEAM_ABBREVIATION"], pdate(r["GAME_DATE"]), "vs." in m,
                       m.split()[-1], r["WL"], r["MIN"], r["PTS"], r["REB"], r["AST"], r["FG3M"], r["STL"], r["BLK"],
                       r["TOV"], nba_season(off)))
    rows.sort(key=lambda g: g["date"], reverse=True)
    return rows

def nba_today_raw():
    sb = scoreboard.ScoreBoard().get_dict()["scoreboard"]
    tm = lambda t: {"code": t["teamTricode"], "name": f'{t.get("teamCity", "")} {t.get("teamName", "")}'.strip()}
    return {"date": sb.get("gameDate", ""), "games": [
        {"id": g["gameId"], "time": g.get("gameTimeUTC", ""), "status": (g.get("gameStatusText") or "").strip(),
         "home": tm(g["homeTeam"]), "away": tm(g["awayTeam"])} for g in sb["games"]]}

# ---------- EuroLeague ----------
def el_season():
    t = dt.date.today(); return f"E{t.year if t.month >= 8 else t.year - 1}"

def el_get(path):
    r = requests.get(EL + path, headers=HDR, timeout=30); r.raise_for_status(); return r

def el_norm(x):
    def side(*names):
        for n in names:
            t = x.get(n)
            if isinstance(t, dict):
                c = t["club"] if isinstance(t.get("club"), dict) else t
                code = c.get("code") or c.get("tvCode") or c.get("abbreviatedName")
                if code: return {"code": str(code), "name": str(c.get("name") or c.get("editorialName") or code)}
    h, a, code = side("local", "home"), side("road", "away"), x.get("gameCode") or x.get("code")
    if not (h and a and code): return None
    raw = str(x.get("date") or x.get("utcDate") or x.get("localDate") or "")
    d = pdate(raw)
    played = x.get("played")
    if played is None: played = bool(d and d < dt.date.today().isoformat())
    return {"code": int(code), "date": d, "time": raw[11:16] if "T" in raw else "", "home": h, "away": a, "played": bool(played)}

def el_schedule(season):
    errs = []
    try:
        js = el_get(f"/v2/competitions/E/seasons/{season}/games").json()
        items = js.get("data") if isinstance(js, dict) else js
        games = [g for g in (el_norm(i) for i in (items or [])) if g]
        if games: return games
        errs.append("v2: no games in response")
    except Exception as e: errs.append(f"v2: {e}")
    try:
        root = ET.fromstring(el_get(f"/v1/schedules?seasonCode={season}").content); games = []
        for it in root.iter():
            k = {c.tag.lower(): (c.text or "").strip() for c in it}
            if "gamecode" in k and "homecode" in k:
                d = pdate(k.get("date", ""))
                pl = k.get("played", "").lower()
                games.append({"code": int(k["gamecode"]), "date": d, "time": k.get("startime", "")[:5],
                              "home": {"code": k["homecode"], "name": k.get("hometeam", k["homecode"])},
                              "away": {"code": k["awaycode"], "name": k.get("awayteam", k["awaycode"])},
                              "played": pl == "true" if pl else bool(d and d < dt.date.today().isoformat())})
        if games: return games
        errs.append("v1: no games in response")
    except Exception as e: errs.append(f"v1: {e}")
    raise RuntimeError("EuroLeague schedule unavailable (" + "; ".join(errs) + ")")

def first(d, *keys):
    for k in keys:
        if d.get(k) is not None: return d[k]
    return 0

def mins(x):
    if isinstance(x, str) and ":" in x:
        try: m, s = x.split(":")[:2]; return float(m) * 60 + float(s)
        except Exception: return 0.0
    return num(x)

def fix_name(n):
    n = (n or "").strip()
    if "," in n: last, fi = n.split(",", 1); n = f"{fi.strip()} {last.strip()}"
    return n.title()

def find_photo(pl):
    for d in (pl, pl.get("person") or {}):
        im = d.get("images")
        if isinstance(im, dict):
            for k in ("headshot", "action", "photo"):
                if im.get(k): return im[k]
    return ""

def el_parse(js, g, season):
    out = []
    for side, home in (("local", True), ("road", False)):
        team, opp = g["home" if home else "away"]["code"], g["away" if home else "home"]["code"]
        rows = []
        for p in (js.get(side) or {}).get("players") or []:
            st, pl = p.get("stats") or {}, p.get("player") or {}
            rows.append((mins(first(st, "timePlayed", "minutes", "time")), st, pl))
        out.append((team, opp, home, rows))
    secs = max([r[0] for o in out for r in o[3]] or [0]) > 60     # API gives seconds
    res = []
    for team, opp, home, rows in out:
        for mn, st, pl in rows:
            mn = mn / 60 if secs else mn
            if mn <= 0: continue
            per = pl.get("person") or {}
            name = fix_name(per.get("name") or pl.get("name"))
            pid = per.get("code") or pl.get("code") or name
            res.append(mk(pid, name, team, g["date"], home, opp, "", mn, first(st, "points"),
                          first(st, "totalRebounds", "rebounds"), first(st, "assistances", "assists"),
                          first(st, "fieldGoalsMade3", "threePointersMade"), first(st, "steals"),
                          first(st, "blocksFavour", "blocks"), first(st, "turnovers"), season, find_photo(pl)))
    return res

EL_LOCK = threading.Lock()
def el_update(force=False):
    season = el_season(); f = CACHE / f"el_{season}.json"
    with EL_LOCK:
        try: store = json.loads(f.read_text(encoding="utf-8"))
        except Exception: store = {"t": 0, "sched": [], "lines": {}}
        if not force and time.time() - store["t"] < 600: return store, False
        try: sched = el_schedule(season)
        except Exception:
            if store["sched"]: return store, True
            raise
        todo = [g for g in sched if g["played"] and str(g["code"]) not in store["lines"]]
        if todo: print(f"EuroLeague: downloading {len(todo)} new game(s)...", flush=True)
        def work(g):
            try: return g, el_parse(el_get(f"/v3/competitions/E/seasons/{season}/games/{g['code']}/stats").json(), g, season)
            except Exception as e: print("  EL game", g["code"], "failed:", e, flush=True); return g, None
        with ThreadPoolExecutor(4) as ex:
            for g, lines in ex.map(work, todo):
                if lines: store["lines"][str(g["code"])] = lines
        store["sched"], store["t"] = sched, time.time()
        f.write_text(json.dumps(store), encoding="utf-8")
        return store, False

def el_logs(force=False):
    st, stale = el_update(force)
    rows = [r for ls in st["lines"].values() for r in ls]
    rows.sort(key=lambda g: g["date"], reverse=True)
    return rows, st, stale

# ---------- shared logic ----------
def logs_for(lg, force=False):
    if lg == "nba": return nba_logs_raw_cached(force)
    return el_logs(force)[0]

def nba_logs_raw_cached(force=False):
    return cached("nba_league_" + nba_season(), nba_logs_raw, force=force)[0]

def today_games(lg, force=False):
    if lg == "nba":
        d, _, _ = cached("nba_today", nba_today_raw, ttl=300, force=force)
        return {"date": d["date"], "label": "Today", "games": d["games"]}
    st, _ = el_update(force); today = dt.date.today().isoformat()
    days = sorted({g["date"] for g in st["sched"] if g["date"] >= today})
    if not days: return {"date": today, "label": "No upcoming games", "games": []}
    d = days[0]
    return {"date": d, "label": "Today" if d == today else "Next game day",
            "games": [{"id": g["code"], "time": g["time"], "status": "Final" if g["played"] else "",
                       "home": g["home"], "away": g["away"]} for g in st["sched"] if g["date"] == d]}

def roster(logs, team, lg):
    out = []
    for pid, gs in by_player(logs).items():
        if gs[0]["team"] != team or not recent(gs[0]): continue
        l5 = gs[:5]; avg = lambda k: round(sum(x[k] for x in l5) / len(l5), 1)
        out.append({"pid": pid, "name": gs[0]["name"], "photo": photo(lg, gs[0]), "gp": len(gs), "min": avg("min"),
                    **{k: avg(k) for k in STATS}, "last": {k: [x[k] for x in l5] for k in STATS}})
    out.sort(key=lambda p: -p["min"])
    return out[:10]

def suggestions(logs, lg, teams, k=10):
    cands = []
    for pid, gs in by_player(logs).items():
        g0 = gs[0]
        if g0["team"] not in teams or not recent(g0): continue
        l5 = gs[:5]
        if len(l5) < 5 or sum(x["min"] for x in l5) / 5 < 18: continue
        for s in STATS:
            v = [x[s] for x in l5]; mean = sum(v) / 5
            if mean < MIN_MEAN[s]: continue
            sd = (sum((x - mean) ** 2 for x in v) / 5) ** .5
            line = min(v) - .5
            n10 = gs[:10]
            cands.append({"pid": pid, "name": g0["name"], "photo": photo(lg, g0), "team": g0["team"], "stat": s,
                          "last5": v, "avg": round(mean, 1), "sd": round(sd, 2), "rsd": sd / mean, "line": line,
                          "h10": [sum(1 for x in n10 if x[s] > line), len(n10)], **teams[g0["team"]]})
    cands.sort(key=lambda c: (c["rsd"], -c["avg"]))
    pick, used, per = [], set(), {}
    for cap in (3, 99):                         # first keep a mix of stats, then fill up
        for c in cands:
            if len(pick) >= k: break
            if c["pid"] in used or per.get(c["stat"], 0) >= cap: continue
            pick.append(c); used.add(c["pid"]); per[c["stat"]] = per.get(c["stat"], 0) + 1
    return pick

# ---------- routes ----------
def api(fn):
    def w(*a, **k):
        try: return jsonify(fn(*a, **k))
        except Exception as e: return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
    w.__name__ = fn.__name__
    return w

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/search")
@api
def search():
    lg, q = request.args.get("lg", "nba"), request.args.get("q", "").strip()
    if len(q) < 2: return []
    if lg == "nba":
        f = [p for p in nba_players.find_players_by_full_name(q) if p["is_active"]]      # active players only
        return [{"id": str(p["id"]), "name": p["full_name"], "team": "",
                 "photo": f"https://cdn.nba.com/headshots/nba/latest/1040x760/{p['id']}.png"} for p in f[:8]]
    out = []
    for pid, gs in by_player(el_logs()[0]).items():
        if norm(q) in norm(gs[0]["name"]): out.append({"id": pid, "name": gs[0]["name"], "team": gs[0]["team"], "photo": gs[0]["photo"]})
    return sorted(out, key=lambda p: p["name"])[:8]

@app.route("/api/games")
@api
def games():
    lg, pid, force = request.args.get("lg", "nba"), request.args.get("pid", ""), request.args.get("refresh") == "1"
    if lg == "nba":
        g, t, stale = cached(f"nba_p{pid}", lambda: nba_player_games(int(pid)), force=force)
        return {"games": g, "fetched": t, "stale": stale}
    rows, st, stale = el_logs(force)
    return {"games": [g for g in rows if g["pid"] == pid], "fetched": st["t"], "stale": stale}

@app.route("/api/today")
@api
def today():
    return today_games(request.args.get("lg", "nba"), request.args.get("refresh") == "1")

@app.route("/api/roster")
@api
def roster_route():
    lg, force = request.args.get("lg", "nba"), request.args.get("refresh") == "1"
    logs = logs_for(lg, force)
    return {"home": roster(logs, request.args["home"], lg), "away": roster(logs, request.args["away"], lg)}

@app.route("/api/suggest")
@api
def suggest():
    lg, force = request.args.get("lg", "nba"), request.args.get("refresh") == "1"
    tg = today_games(lg, force); teams = {}
    for g in tg["games"]:
        if g["status"] == "Final": continue
        teams[g["home"]["code"]] = {"opp": g["away"]["code"], "home": True, "time": g["time"]}
        teams[g["away"]["code"]] = {"opp": g["home"]["code"], "home": False, "time": g["time"]}
    return {"date": tg["date"], "label": tg["label"], "picks": suggestions(logs_for(lg, force), lg, teams)}

@app.route("/api/el/debug")
def el_debug():
    """Open http://localhost:5000/api/el/debug if EuroLeague data looks wrong and send the text to Claude."""
    s = el_season(); out = {"season": s}
    for name, path in (("schedule_v2", f"/v2/competitions/E/seasons/{s}/games"), ("stats_game1", f"/v3/competitions/E/seasons/{s}/games/1/stats")):
        try: out[name] = el_get(path).text[:1500]
        except Exception as e: out[name] = f"ERROR {e}"
    return jsonify(out)

def warm():
    for f in (el_update, nba_logs_raw_cached):
        try: f()
        except Exception as e: print("warm-up skipped:", e, flush=True)

if __name__ == "__main__":
    threading.Thread(target=warm, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False)

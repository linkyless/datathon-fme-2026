"""The LIVE service: answers every shopper of shop.requests with 10 products in under 3 seconds.

    python -m src.service --no-send                 # test: does everything except sending (always safe)
    python -m src.service --no-send --duration 120
    python -m src.service --handover                # LIVE: gets ready and waits for Enter before sending
    python -m src.service                           # LIVE with the final model, AP7 (weights "aphc")
    python -m src.service --arms ap8                # LIVE replaying one of the A/B tests of the event (see ARM_SETS)

Only ONE instance may run at a time: two instances = duplicated responses.

Three threads (parallel workers inside the same program):
  1. POLLING (the main one): asks RawTree for the requests of the last 15 s, answers the new ones with the learner
     (src/learner.py), validates every response and puts the batch in the send queue. No pauses.
  2. SENDING: takes batches from the queue and inserts them into team_NN.submissions. Polling never waits for it.
  3. REFRESH: keeps the shop up to date without blocking the other two (stock every 1 s, catalog every 15 s, best
     sellers every 10 min). If a refresh fails, the previous version is kept. The learner's World has its own
     thread that applies shop.events every 1.5 s.

Guarantees: one single response per request_id (also after a restart), always 10 valid IDs (if the model fails or
returns something invalid, the available best sellers are sent) and it never goes down.
Measured on Sat 13:55: each trip to RawTree costs ~300 ms and the laptop clock runs ~8 s behind the server, so the
age of each request is computed ON THE SERVER (column age_ms), never with the laptop clock.
"""
from __future__ import annotations

import argparse
import dataclasses
import queue
import subprocess
import threading
import time
import zlib
from pathlib import Path

from src import catalog, learner
from src.rawtree import TEAM, insert, query
from src.validate import response_errors

MODEL_NAME = "e-samen-v1"
"""Public name (model_version = name + arm suffix, e.g. e-samen-v1-AP7). It shows on the leaderboard: no hashes."""

POLL = """
SELECT request_id::String AS request_id, ts, user_id, is_new_user, context, recent_views,
       dateDiff('millisecond', ts::DateTime64(3), now64(3)) AS age_ms
FROM shop.requests
WHERE ts > now() - INTERVAL 15 SECOND
ORDER BY ts DESC
LIMIT 300"""
"""Only the last 15 s: the starter's query (no time filter) took up to 10 s."""

ALREADY_SENT = """
SELECT request_id::String AS request_id
FROM {team}.submissions
WHERE request_id::String IN (
    SELECT request_id::String FROM shop.requests WHERE ts > now() - INTERVAL 10 MINUTE)"""
"""What was already answered (by us or by the baseline): not repeated at startup. Fails without ::String."""

LOG = Path(__file__).resolve().parent.parent / "logs" / "service.log"

SEND_THREADS = 3
"""Parallel send workers (measured: one insert takes 0.3-1 s)."""
_LOCK = threading.Lock()

ARM_SETS: dict[str, list[tuple[str, dict]]] = {
    "ap7": [("-AP7", {"learner": "aphc"})],
    "ap8": [("-AP7", {"learner": "aphc"}), ("-AP8", {"learner": "ap8"})],
    "ap8-only": [("-AP8", {"learner": "ap8"})],
    "apoh2": [("-APHC", {"learner": "aphc"}), ("-APOH2", {"learner": "apo2_s", "new_user_model": "aph1_n"})],
    "aph2": [("-APHC", {"learner": "aphc"}), ("-APH2", {"learner": "aphc", "new_user_model": "aph1_n"})],
    "apoh": [("-APHC", {"learner": "aphc"}), ("-APOH1", {"learner": "apoh1_c", "new_user_model": "apoh1_n"})],
    "aph": [("-APHC", {"learner": "aphc"}), ("-APH1", {"learner": "aph1_c", "new_user_model": "aph1_n"})],
    "apo2": [("-APOC", {"learner": "apoc_s"}), ("-APO2", {"learner": "apo2_s"})],
    "apo": [("-APOC", {"learner": "apoc_s"}), ("-APO1", {"learner": "apo1_s"}), ("-APO2", {"learner": "apo2_s"})],
    "ap7-test": [("-AP7C", {"learner": "ap7c"}), ("-AP7", {"learner": "ap7"}),
                 ("-AP7S", {"learner": "ap7", "force": {"same_sub": 3.0}})],
    "ap3": [("-AP3", {"learner": "ap3"}), ("-EXP1", {"learner": "ap3", "explore": True})],
    "ap3-only": [("-AP3", {"learner": "ap3"})],
    "ap2": [("-C", {})] + [("-AP2", {"learner": None})] * 3,
}
"""Every arm set the learner ran with during the event (results in docs/EXPERIMENTS.md). Each shopper ALWAYS falls in
the same arm: crc32(user_id) % len(arms). Each arm = (name suffix, params):
  learner          name of the trained model (data/learner_<name>.json; None = data/learner.json)
  new_user_model   hybrid: a different model for new shoppers (is_new_user)
  explore          slots 9-10 explore in half of the requests (those are labelled with an extra "x")
  force            {signal: multiplier} instead of the learned weight
  (no learner)     rule "C": best sellers of the category of the last viewed product (e-samen-v0)
  ap7       FINAL: the per-product learner with same_sub, retrained on Sun 03:40 ("aphc"), 100 %.
  ap8       AP7 vs the choice model AP8 (Sun 06:15-07:26): a tie, 1.77 vs 1.69 expected purchases/100.
  apoh2     new shoppers -> APH1 specialist, known -> APO2 (Sun 04:54-06:15): 1.75 control vs 1.58.
  aph/aph2/apoh/apo/apo2  hybrid and organic-history variants (Sun early morning).
  ap7-test  same_sub learned (×1.39) and forced (×3) vs a control without it (Sun 02:45).
  ap3       exploration in slots 9-10: costs ~0.3 purchases/100 in the requests that explore (Sat 22:00).
  ap2       the first learner vs rule C (Sat 20:50): 2.03 % vs 0.83 % real purchases, CI [+0.56, +1.87].
Earlier versions (rules, co-visits, R5 regression, R6 tree, v2 choice, the AP4-AP6 ordering arms) ran from other
code: they are kept in src/ and under the git tags version/*."""
ARMS = ARM_SETS["ap7"]

WORLD: "learner.World | None" = None
LEARNER_MODELS: dict = {}
"""model name (None = data/learner.json, "aphc" = data/learner_aphc.json) -> Model."""


@dataclasses.dataclass
class Shop:
    """What the service needs besides the learner. Each field is replaced WHOLE by the refresh thread."""
    catalog: dict[str, dict]
    available: set[str]
    popular: list[str]
    popular_by_category: dict[str, list[str]]


def prepare_learner(shop: Shop, stop: threading.Event) -> None:
    """Own thread: loads the models the arms ask for and the World (history + today's events), and keeps it up to
    date every 1.5 s. Never touches the stock. While something is missing, those arms answer as C (labelled -C)."""
    global WORLD
    names = {p["learner"] for _, p in ARMS if "learner" in p}
    names |= {p["new_user_model"] for _, p in ARMS if p.get("new_user_model")}
    while not stop.is_set() and WORLD is None:
        try:
            t = time.monotonic()
            models = {n: learner.Model.load(n) for n in names}
            missing = [str(n or "learner") for n, m in models.items() if m is None]
            if missing:  # switches on by itself as soon as they exist (python -m src.learner [--name X] creates them)
                log(f"learner: models {missing} missing; those arms answer as C (retry in 30 s)")
                stop.wait(30)
                continue
            world = learner.World.up_to_date(shop.catalog)
            LEARNER_MODELS.update(models)
            WORLD = world
            log(f"learner ready in {time.monotonic() - t:.1f} s ({len(world.bought)} shoppers with purchases; "
                f"models {sorted(str(n or 'learner') for n in models)})")
        except Exception as e:
            log(f"learner: could not load the World (retry in 30 s): {e}")
            stop.wait(30)
    failures = 0
    while not stop.is_set():
        try:
            if WORLD.catalog is not shop.catalog:
                WORLD.catalog = shop.catalog
            WORLD.catch_up()
            failures = 0
        except Exception as e:
            failures += 1
            if failures in (1, 10, 100):
                log(f"learner: catch_up failed ({failures} in a row): {e}")
        stop.wait(1.5)


def arm(request: dict) -> tuple[str, dict]:
    """Picks the shopper's arm. zlib.crc32 gives the same number on every restart (Python's hash() does not)."""
    if len(ARMS) == 1:
        return ARMS[0]
    key = str(request.get("user_id") or request.get("request_id", ""))
    return ARMS[zlib.crc32(key.encode()) % len(ARMS)]


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def current_commit() -> str:
    """Short hash of the running commit. Only for the log (NOT in the public name)."""
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return h.stdout.strip() or "no-git"
    except Exception:
        return "no-git"


# ---------------------------------------------------------------- the shop and its refresh (thread 3)
def initial_shop() -> Shop:
    """The minimum to answer well (catalog, stock, best sellers)."""
    while True:
        try:
            cat = catalog.load_catalog()
            out = catalog.load_out_of_stock()
            pop = catalog.load_popular()
            return Shop(catalog=cat, available=catalog.available(cat, out), popular=pop,
                        popular_by_category=catalog.by_category(cat, pop))
        except Exception as e:
            log(f"initial shop failed, retry in 3 s: {e}")
            time.sleep(3)


def refresh(shop: Shop, stop: threading.Event) -> None:
    """Thread 3. Each piece is replaced WHOLE (never modified halfway): the polling thread always sees a complete
    version, the old one or the new one."""
    last: dict[str, float] = {}
    out_of_stock: set[str] = set()

    def attempt(name: str, every_s: float, action) -> None:
        """Each piece on its own: if one fails (network...), the others go on, and the failed one is retried in
        60 s at most."""
        if time.monotonic() - last.get(name, -1e9) < every_s:
            return
        last[name] = time.monotonic()
        try:
            action()
        except Exception as e:
            last[name] = time.monotonic() - every_s + min(60, every_s)
            log(f"refresh of {name} failed (keeping the previous one): {e}")

    def stock() -> None:
        nonlocal out_of_stock
        out_of_stock = catalog.load_out_of_stock()
        shop.available = catalog.available(shop.catalog, out_of_stock)

    def cat() -> None:
        shop.catalog = catalog.load_catalog()
        shop.available = catalog.available(shop.catalog, out_of_stock)
        shop.popular_by_category = catalog.by_category(shop.catalog, shop.popular)

    def popular() -> None:
        shop.popular = catalog.load_popular()
        shop.popular_by_category = catalog.by_category(shop.catalog, shop.popular)

    while not stop.is_set():
        attempt("stock", 1, stock)
        attempt("catalog", 15, cat)
        attempt("popular", 600, popular)
        stop.wait(0.2)


# ---------------------------------------------------------------- answering one request
def category_best_sellers(request: dict, shop: Shop, available: set[str]) -> list[str]:
    """Rule C (e-samen-v0): best sellers of the category of the last viewed product, then all-time best sellers."""
    viewed = learner._viewed_products(request.get("recent_views"))
    category = str(shop.catalog.get(viewed[-1], {}).get("category", "")) if viewed else ""
    items: list[str] = []
    for source in (shop.popular_by_category.get(category, []), shop.popular, sorted(available)):
        for p in source:
            if len(items) == 10:
                return items
            if p in available and p not in items:
                items.append(p)
    return items


def safe_response(request: dict, shop: Shop) -> tuple[dict, bool]:
    """Returns (submission, was_replaced). If the model fails or is invalid: available best sellers."""
    available = shop.available  # a single read: the same version to recommend and to validate
    suffix, params = arm(request)
    mv = MODEL_NAME + suffix
    try:
        if "learner" in params:
            model = LEARNER_MODELS.get(params["learner"])
            if params.get("new_user_model") and learner._parse_request(request)[2]:  # hybrid: new shopper
                model = LEARNER_MODELS.get(params["new_user_model"])
            if WORLD is not None and model is not None:
                explore = bool(params.get("explore"))
                items = learner.recommend(request, WORLD, model, available, explore=explore, force=params.get("force"))
                if not response_errors(items, available):
                    if explore and learner.should_explore(request.get("request_id") or ""):
                        mv += "x"  # the half that explores, with its own leaderboard row
                    return {"request_id": request["request_id"], "items": items, "model_version": mv}, False
            mv = MODEL_NAME + "-C"  # no World yet (or an odd answer): rule C, and labelled as C
        items = category_best_sellers(request, shop, available)
        if not response_errors(items, available):
            return {"request_id": request["request_id"], "items": items, "model_version": mv}, False
    except Exception as e:
        log(f"recommend failed for {request.get('request_id')}: {e}")
    # Fallback: available best sellers. If fewer than 10, filled with anything available (sending <10 is invalid;
    # available best sellers can drop below 10 if many are out of stock). Validated before leaving, just in case.
    items = [p for p in shop.popular if p in available][:10]
    if len(items) < 10:
        for p in sorted(available):
            if len(items) == 10:
                break
            if p not in items:
                items.append(p)
    if response_errors(items, available):
        log(f"invalid fallback for {request.get('request_id')}: available={len(available)}")
    return {"request_id": request["request_id"], "items": items, "model_version": mv}, True


# ---------------------------------------------------------------- sending (thread 2)
def _not_yet_sent(batch: list[dict]) -> list[dict] | None:
    """Those of the batch that are NOT in submissions. None if the query fails."""
    ids = ", ".join("'" + str(f["request_id"]).replace("'", "") + "'" for f in batch)
    try:
        sent = {f["r"] for f in query(f"SELECT request_id::String r FROM {TEAM}.submissions WHERE request_id::String IN ({ids})",
                                      timeout=2)}
    except Exception:
        return None
    return [f for f in batch if f["request_id"] not in sent]


def send(q: queue.Queue, no_send: bool, stats: dict) -> None:
    while True:
        batch = q.get()
        if batch is None:
            return
        t = time.perf_counter()
        sent = False
        for attempt_no in (1, 2):
            try:
                if not no_send:
                    insert("submissions", batch, timeout=3)
                sent = True
                with _LOCK:  # several threads add at once: without the lock counts would be lost
                    stats["sent"] += len(batch)
                break
            except Exception as e:
                log(f"send failed (attempt {attempt_no}): {e}")
                if attempt_no == 1 and "Read timed out" in str(e):
                    # The batch LEFT and the confirmation did not arrive: it may have gone in. Resending blindly
                    # duplicated responses (measured Sat 20:45: 7 duplicates in 20 s). Only what is missing is resent.
                    batch = _not_yet_sent(batch)
                    if batch is None:  # could not check: better lose them than send them twice
                        log("could not check what went in: no retry (avoids duplicates)")
                        sent = True
                        break
                    if not batch:
                        sent = True
                        log("the batch had gone in: not resent")
                        break
        if not sent:  # two failed attempts: those requests end up as "missing" in the feedback
            with _LOCK:
                stats["lost"] += len(batch)
            log(f"batch lost after 2 attempts: {len(batch)} requests")
        with _LOCK:
            stats["send_ms"].append((time.perf_counter() - t) * 1000)


# ---------------------------------------------------------------- polling (thread 1, the main one)
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true", help="do everything except inserting into submissions")
    ap.add_argument("--handover", action="store_true", help="get ready and wait for Enter before starting")
    ap.add_argument("--arms", choices=sorted(ARM_SETS), default="ap7", help="arm set (default ap7, the final model)")
    ap.add_argument("--duration", type=float, default=0, help="seconds until it stops by itself (0 = never)")
    args = ap.parse_args()
    global ARMS
    ARMS = ARM_SETS[args.arms]

    log(f"starting {MODEL_NAME} ({current_commit()}) | team {TEAM} | {'NO SEND (test)' if args.no_send else 'LIVE'} "
        f"| arms {[a for a, _ in ARMS]}")
    shop = initial_shop()
    log(f"initial shop ready: {len(shop.catalog)} products, {len(shop.available)} available")
    stop = threading.Event()
    threading.Thread(target=refresh, args=(shop, stop), daemon=True, name="refresh").start()
    if any("learner" in p for _, p in ARMS):
        threading.Thread(target=prepare_learner, args=(shop, stop), daemon=True, name="learner").start()

    if args.handover and not args.no_send:
        input(">>> READY. Stop the other service (Ctrl+C in its terminal) and press Enter HERE right away... ")

    seen: set[str] = set()
    while True:  # the answered ones are read RIGHT before starting: so the handover does not duplicate
        try:
            seen = {f["request_id"] for f in query(ALREADY_SENT.format(team=TEAM), timeout=10)}
            break
        except Exception as e:
            log(f"could not read the already sent ones, retrying: {e}")
            time.sleep(1)
    log(f"{len(seen)} requests of the last 10 min were already answered: not repeated")

    stats = {"sent": 0, "replaced": 0, "lost": 0, "send_ms": [], "age_ms": [], "poll_failures": 0}
    q: queue.Queue = queue.Queue()
    # Several send workers: an insert takes up to ~1 s and, with only one, the next batch waited in the queue.
    senders = [threading.Thread(target=send, args=(q, args.no_send, stats), daemon=True, name=f"send{i}")
               for i in range(SEND_THREADS)]
    for h in senders:
        h.start()

    start = last_log = time.monotonic()
    try:
        while not args.duration or time.monotonic() - start < args.duration:
            try:
                rows = query(POLL, timeout=2)
            except Exception as e:  # a hung query cannot stop the service
                stats["poll_failures"] += 1
                if stats["poll_failures"] % 10 == 1:
                    log(f"poll failed ({stats['poll_failures']} in total): {e}")
                time.sleep(0.05)
                continue
            new = [f for f in rows if f["request_id"] not in seen]
            batch = []
            for r in new:
                seen.add(r["request_id"])
                sub, replaced = safe_response(r, shop)
                stats["replaced"] += replaced
                stats["age_ms"].append(float(r.get("age_ms") or 0))
                batch.append(sub)
            if batch:
                q.put(batch)
            if time.monotonic() - last_log >= 10:
                last_log = time.monotonic()
                age = sorted(stats["age_ms"]) or [0]
                send_ms = sorted(stats["send_ms"]) or [0]
                log(f"{'(no send) ' if args.no_send else ''}answered={stats['sent']} "
                    f"replaced={stats['replaced']} poll_failures={stats['poll_failures']} | "
                    f"age when seen p50={age[len(age) // 2]:.0f} p95={age[int(len(age) * .95)]:.0f} ms | "
                    f"send p95={send_ms[int(len(send_ms) * .95)]:.0f} ms | queue={q.qsize()}")
                stats["age_ms"].clear()
                stats["send_ms"].clear()
    except KeyboardInterrupt:
        log("stopping (Ctrl+C)...")
    finally:
        stop.set()
        for _ in senders:
            q.put(None)
        for h in senders:
            h.join(timeout=5)
        log(f"stopped. Answered in total: {stats['sent']}, replaced: {stats['replaced']}, "
            f"lost by failed sends: {stats['lost']}")


if __name__ == "__main__":
    main()

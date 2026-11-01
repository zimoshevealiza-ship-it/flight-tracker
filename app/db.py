import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    key            TEXT PRIMARY KEY,
    provider       TEXT,
    airline        TEXT,
    airline_name   TEXT,
    flight_number  TEXT,
    origin         TEXT,
    destination    TEXT,
    depart_date    TEXT,
    depart_time    TEXT,
    stops          INTEGER,
    duration_min   INTEGER,
    first_seen     TEXT,
    last_seen      TEXT,
    first_price    REAL,
    last_price     REAL,
    min_price      REAL,
    max_price      REAL,
    currency       TEXT,
    link           TEXT,
    last_obs       TEXT
);
CREATE INDEX IF NOT EXISTS ix_offers_route ON offers(origin, destination, depart_date);
CREATE INDEX IF NOT EXISTS ix_offers_airline ON offers(airline);

CREATE TABLE IF NOT EXISTS observations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_key      TEXT,
    observed_at    TEXT,
    price          REAL,
    days_to_dep    INTEGER,
    airline        TEXT,
    destination    TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_offer ON observations(offer_key, observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_airline ON observations(airline);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT,
    type           TEXT,
    offer_key      TEXT,
    airline        TEXT,
    destination    TEXT,
    depart_date    TEXT,
    old_price      REAL,
    new_price      REAL,
    message        TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(created_at);

CREATE TABLE IF NOT EXISTS route_scans (
    provider       TEXT,
    destination    TEXT,
    depart_date    TEXT,
    first_scan     TEXT,
    last_scan      TEXT,
    scans          INTEGER DEFAULT 0,
    PRIMARY KEY (provider, destination, depart_date)
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT,
    finished_at    TEXT,
    requests       INTEGER,
    offers         INTEGER,
    events         INTEGER,
    errors         TEXT
);

CREATE TABLE IF NOT EXISTS state (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


class DB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(offers)")}
        if "last_obs" not in cols:
            self.conn.execute("ALTER TABLE offers ADD COLUMN last_obs TEXT")
        self.conn.commit()

    def q(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def x(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def many(self, sql, seq):
        with self.lock:
            self.conn.executemany(sql, seq)
            self.conn.commit()

    def prune(self, now_iso, keep_days=365):
        """Drop very old history so the file stays small."""
        self.x("DELETE FROM observations WHERE observed_at < datetime(?, ?)", (now_iso, f"-{keep_days} days"))
        self.x("DELETE FROM offers WHERE depart_date < date(?, ?)", (now_iso, f"-{keep_days} days"))
        self.x("DELETE FROM events WHERE created_at < datetime(?, '-60 days')", (now_iso,))
        self.x("DELETE FROM runs WHERE started_at < datetime(?, '-30 days')", (now_iso,))

    def get_state(self, k, default=None):
        r = self.one("SELECT v FROM state WHERE k=?", (k,))
        return r["v"] if r else default

    def set_state(self, k, v):
        self.x("INSERT INTO state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

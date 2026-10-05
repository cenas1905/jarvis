"""Scoped phone-presence leases; stale/missing state always closes the mic."""
import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from app_paths import data_path

DB_PATH = data_path("memory", "home_presence.sqlite3")
LEASE_SECONDS = 240


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH,timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("CREATE TABLE IF NOT EXISTS presence(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,token_hash TEXT NOT NULL,expires REAL NOT NULL)")
        con.execute("INSERT OR IGNORE INTO presence VALUES(1,0,'',0)")
        with con:
            yield con
    finally:
        con.close()


def pair_phone():
    token = secrets.token_urlsafe(32)
    with db() as con:
        con.execute("UPDATE presence SET token_hash=?,enabled=1,expires=0 WHERE id=1",
                    (hashlib.sha256(token.encode()).hexdigest(),))
    return token


def status(now=None):
    now = time.time() if now is None else now
    try:
        with db() as con:
            row = con.execute("SELECT enabled,expires FROM presence WHERE id=1").fetchone()
        return {"enabled":bool(row["enabled"]),"home":bool(row["enabled"] and row["expires"]>now),
                "expires_in":max(0,round(row["expires"]-now)),"lease_seconds":LEASE_SECONDS}
    except (OSError,sqlite3.Error):
        return {"enabled":True,"home":False,"expires_in":0,"error":"presence_store_unavailable"}


def heartbeat(token, home=True, now=None):
    now = time.time() if now is None else now
    digest = hashlib.sha256(str(token).encode()).hexdigest()
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM presence WHERE id=1").fetchone()
        if not row["enabled"] or not row["token_hash"] or not secrets.compare_digest(digest,row["token_hash"]):
            raise PermissionError("Geçersiz eve-geliş anahtarı")
        con.execute("UPDATE presence SET expires=? WHERE id=1",(now+LEASE_SECONDS if home else 0,))
    return status(now)


def disable():
    with db() as con:
        con.execute("UPDATE presence SET enabled=0,expires=0,token_hash='' WHERE id=1")


def allowed():
    state = status()
    return not state["enabled"] or state["home"]

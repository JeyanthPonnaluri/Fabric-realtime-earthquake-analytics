"""
USGS real-time earthquake feed -> Microsoft Fabric Eventstream (custom endpoint)

Polls the USGS GeoJSON summary feed, flattens each event, and sends only
new or revised events to a Fabric Eventstream "Custom endpoint" source
(Event Hub-compatible). USGS revises events (magnitude, location, review
status) after first publication, so (id, updated) is used as the change key.

Setup
-----
pip install azure-eventhub requests

In Fabric: Eventstream -> Add source -> Custom endpoint -> Event Hub protocol
-> copy "Connection string-primary key" and the Event Hub name.

    set EVENTSTREAM_CONN_STR="Endpoint=sb://...;SharedAccessKeyName=...;SharedAccessKey=...;EntityPath=es_xxx"
    set EVENTSTREAM_NAME="es_xxx"

Run
---
    python usgs_to_eventstream.py --backfill month   # one-off: load last 30 days
    python usgs_to_eventstream.py                    # continuous: poll every 60s
    python usgs_to_eventstream.py --dry-run          # print instead of sending
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests

FEEDS = {
    "hour": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
    "day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "week": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson",
    "month": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson",
}

log = logging.getLogger("usgs-producer")


def ms_to_iso(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def region_from_place(place):
    # "12 km SSW of Volcano, Hawaii" -> "Hawaii"; "Fiji region" -> "Fiji region"
    if not place:
        return "Unknown"
    return place.split(", ")[-1].strip() if ", " in place else place.strip()


def flatten(feature):
    p = feature.get("properties", {}) or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
    lon, lat, depth = (coords + [None, None, None])[:3]
    return {
        "EventId": feature.get("id"),
        "Updated": ms_to_iso(p.get("updated")),
        "EventTime": ms_to_iso(p.get("time")),
        "Magnitude": p.get("mag"),
        "MagType": p.get("magType"),
        "Place": p.get("place"),
        "Region": region_from_place(p.get("place")),
        "Latitude": lat,
        "Longitude": lon,
        "DepthKm": depth,
        "Significance": p.get("sig"),
        "Tsunami": p.get("tsunami"),
        "Felt": p.get("felt"),
        "Cdi": p.get("cdi"),
        "Mmi": p.get("mmi"),
        "Alert": p.get("alert"),
        "Status": p.get("status"),
        "EventType": p.get("type"),
        "Network": p.get("net"),
        "Nst": p.get("nst"),
        "Gap": p.get("gap"),
        "Rms": p.get("rms"),
        "Dmin": p.get("dmin"),
        "Url": p.get("url"),
        "ProducedAt": datetime.now(timezone.utc).isoformat(),
    }


def fetch(feed_url, session):
    r = session.get(feed_url, timeout=30, headers={"User-Agent": "fabric-rti-demo/1.0"})
    r.raise_for_status()
    return r.json().get("features", [])


class Sender:
    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.producer = None
        if not dry_run:
            from azure.eventhub import EventHubProducerClient

            conn = os.environ.get("EVENTSTREAM_CONN_STR")
            name = os.environ.get("EVENTSTREAM_NAME")
            if not conn:
                sys.exit("EVENTSTREAM_CONN_STR is not set (see header of this script).")
            self.producer = EventHubProducerClient.from_connection_string(conn, eventhub_name=name)

    def send(self, records):
        if not records:
            return
        if self.dry_run:
            for rec in records[:5]:
                print(json.dumps(rec))
            if len(records) > 5:
                print(f"... and {len(records) - 5} more")
            return
        from azure.eventhub import EventData

        batch = self.producer.create_batch()
        for rec in records:
            ev = EventData(json.dumps(rec))
            try:
                batch.add(ev)
            except ValueError:  # batch full
                self.producer.send_batch(batch)
                batch = self.producer.create_batch()
                batch.add(ev)
        self.producer.send_batch(batch)

    def close(self):
        if self.producer:
            self.producer.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", choices=["day", "week", "month"], help="send a larger window once, then exit")
    ap.add_argument("--interval", type=int, default=60, help="poll interval seconds (USGS updates ~every minute)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    session = requests.Session()
    sender = Sender(args.dry_run)
    seen = {}  # EventId -> last Updated sent

    try:
        if args.backfill:
            recs = [flatten(f) for f in fetch(FEEDS[args.backfill], session)]
            sender.send(recs)
            log.info("Backfill (%s): sent %d events", args.backfill, len(recs))
            return

        # Prime with the last day so a restart doesn't miss recent revisions.
        feed = FEEDS["day"]
        while True:
            try:
                changed = []
                for f in fetch(feed, session):
                    rec = flatten(f)
                    if seen.get(rec["EventId"]) != rec["Updated"]:
                        seen[rec["EventId"]] = rec["Updated"]
                        changed.append(rec)
                sender.send(changed)
                log.info("Sent %d new/revised events (tracking %d)", len(changed), len(seen))
                feed = FEEDS["hour"]
                if len(seen) > 50000:  # keep memory bounded
                    seen = dict(list(seen.items())[-20000:])
            except requests.RequestException as e:
                log.warning("Fetch failed: %s (will retry)", e)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        sender.close()


if __name__ == "__main__":
    main()

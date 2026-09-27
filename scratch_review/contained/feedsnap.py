"""Record / replay the live national feed payloads (2026-09-27 contained-fire
study). NOT product code.

record: fetch WFIGS points + perimeters (with the containment/control/out +
attr_IncidentSize fields the new adapter asks for — a superset, so the
legacy parse reads the same bodies), FIRIS and CAL FIRE over the CONUS
envelope through the real adapters' query code, and write every raw body to
out/contained/snapshot/.

replay: ReplayHttp(dir) is an http_get that serves the recorded body for any
URL of the same layer (outFields ignored), so feeds.WfigsFeed / FirisFeed /
CalFireFeed parse the recorded world byte for byte, offline."""
import json
import os
import sys
import time
import urllib.parse

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, WT)

from wgresponse import feeds  # noqa: E402
from wgresponse.contracts import BBox  # noqa: E402

CONUS = BBox(west=-125.0, south=24.0, east=-66.0, north=50.0)
#: data (recorded payloads, caches, results) live in the gitignored out/contained/
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "out", "contained")
SNAP = os.path.join(DATA, "snapshot")

POINT_FIELDS = ("IncidentName,IrwinID,IncidentSize,PercentContained,"
                "FireDiscoveryDateTime,ModifiedOnDateTime_dt,POOState,"
                "IncidentTypeCategory,ContainmentDateTime,ControlDateTime,"
                "FireOutDateTime")
PERIM_FIELDS = ("poly_IncidentName,attr_IncidentName,poly_IRWINID,attr_IrwinID,"
                "poly_GISAcres,attr_CalculatedAcres,attr_FinalAcres,"
                "attr_PercentContained,attr_FireDiscoveryDateTime,"
                "attr_ModifiedOnDateTime_dt,poly_DateCurrent,"
                "attr_ContainmentDateTime,attr_ControlDateTime,"
                "attr_FireOutDateTime,attr_IncidentSize")


def _layer_key(url: str) -> str:
    u = urllib.parse.urlparse(url)
    if "/services/" in u.path:
        return u.path.split("/services/")[-1].split("/")[0]
    return u.netloc + u.path.replace("/", "_")


class RecordingHttp:
    def __init__(self):
        self.bodies = {}

    def __call__(self, url, headers=None):
        status, body = feeds._default_http_get(url, headers, timeout_s=120.0)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        key = _layer_key(url) + "@" + q.get("resultOffset", ["0"])[0]
        self.bodies[key] = (status, body)
        return status, body


class ReplayHttp:
    def __init__(self, snap_dir=SNAP):
        self.dir = snap_dir
        with open(os.path.join(snap_dir, "manifest.json"), encoding="utf-8") as f:
            self.manifest = json.load(f)

    def __call__(self, url, headers=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        key = _layer_key(url) + "@" + q.get("resultOffset", ["0"])[0]
        ent = self.manifest["bodies"].get(key)
        if ent is None:
            raise RuntimeError(f"no recorded body for {key}")
        with open(os.path.join(self.dir, ent["file"]), "rb") as f:
            return ent["status"], f.read()


def record():
    os.makedirs(SNAP, exist_ok=True)
    rec = RecordingHttp()
    t = time.time()
    w = feeds.WfigsFeed(http_get=rec)
    w.POINT_OUT_FIELDS = POINT_FIELDS
    w.PERIM_OUT_FIELDS = PERIM_FIELDS
    incs = w.fetch_active(CONUS)
    fi = feeds.FirisFeed(http_get=rec)
    fincs = fi.fetch_active(CONUS)
    cf = feeds.CalFireFeed(http_get=rec)
    cincs = cf.fetch_active(CONUS)
    manifest = {"recorded_at_s": t, "recorded_utc": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), "bodies": {},
        "counts": {"wfigs": len(incs), "wfigs_ok": w.last_fetch_ok,
                   "firis": len(fincs), "firis_ok": fi.last_fetch_ok,
                   "calfire": len(cincs), "calfire_ok": cf.last_fetch_ok}}
    for key, (status, body) in rec.bodies.items():
        fn = key.replace("@", "_at_").replace(":", "_") + ".body"
        with open(os.path.join(SNAP, fn), "wb") as f:
            f.write(body)
        manifest["bodies"][key] = {"file": fn, "status": status,
                                   "bytes": len(body)}
    with open(os.path.join(SNAP, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "record":
        record()

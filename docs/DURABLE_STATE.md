# Durable state — the S3 mirror

**Problem (tri-model review 2026-08-31, fix program item "durable state off
/tmp").** The live watcher runs on AWS App Runner with its state directory on
`/tmp`. App Runner restarted the container ~37 times in August. Every restart
wiped:

| lost on restart | consequence |
| --- | --- |
| the alert-ladder records (`AlertRecord` per client×incident) | the ladder restarts at NONE |
| the notified ledger (what was actually sent) | **already-sent notifications can re-fire** |
| the incumbent dispatch plan | cold-start plan, needless churn |
| the LANDFIRE terrain tile cache (`lfps_<hash>.wgtile`) and the raw LFPS job zips (`lfps_<hash>.zip`) | every tile re-fetched through the LFPS job API — the first cycle after a restart took **~20 min** |

The fix is a **mirror**, not a migration: local disk stays the authority and
keeps its atomic-write discipline; the same bytes are copied to S3 so the
*next* container can pick them up. Nothing in the alert loop depends on S3
being reachable.

---

## 1. What is mirrored, and where

One bucket + prefix (`s3://<bucket>/<prefix>`) holds everything:

| local file | S3 object key | written by |
| --- | --- | --- |
| `<state_dir>/wgresponse_state.json` | `<prefix>/state.json` | `persist.StateStore` |
| `<state_dir>/terrain_cache/lfps_<hash>.wgtile` (decoded tile) | `<prefix>/terrain/lfps_<hash>.wgtile` | `terrain.LfpsTerrain` |
| `<state_dir>/terrain_cache/lfps_<hash>.zip` (raw LFPS job output) | `<prefix>/terrain/lfps_<hash>.zip` | `terrain.LfpsTerrain` |

* The state object is deliberately named `state.json`, not the local
  filename — the key is the operator-facing contract, the local name is an
  implementation detail (`persist.S3_STATE_OBJECT`).
* Terrain keys reuse the **existing deterministic cache filenames**: the
  `.wgtile` hash covers `(layer_list, AOI, cell_m)`, the `.zip` hash covers
  `(layer_list, AOI)` only (the zip is raw job output; `cell_m` is a decode-time
  resample). A mirror is therefore just a `cache_dir` shared across containers,
  and it is safe for several watchers to share one prefix.
* Terrain objects are immutable-by-content: the same key always means the same
  tile, so they never need cache invalidation. They can be deleted freely (the
  next fetch re-creates them) — see the lifecycle note in §5.

### NOT mirrored (by design or still open)

* **`<state_dir>/ses_send_ledger.jsonl`** (`notify.SesNotifier`) — the SES
  notifier's own JSON-lines dedupe ledger. It is a *second* layer of dedupe;
  the primary one (the engine's `notified_ledger`) IS mirrored inside
  `state.json`, so a restart still re-fires nothing in the normal case. Owner:
  `notify.py` — **open follow-up**, it is the last durable file left on `/tmp`.
* Weather/feed responses, incident snapshots, logs, the HTTP console state —
  all derived per cycle, cheap to recompute, deliberately not mirrored.
* Nothing is mirrored when no `s3_uri` is configured: the code paths are
  byte-identical to the pre-mirror behaviour (no imports, no network).

---

## 2. Env-var contract (for the CLI wiring)

```
WGRESP_STATE_S3=s3://<bucket>/<prefix>
```

* **Unset or empty ⇒ mirror OFF** (local disk only, exactly as before). An
  empty value means off so an unset-but-declared App Runner variable is not a
  startup failure.
* A value that is not an `s3://bucket[/prefix]` URI raises **`ValueError` at
  construction** — a typo must fail loudly at startup, never degrade silently
  into an unmirrored daemon.
* Trailing slashes are ignored; `s3://bucket` (no prefix) is legal and puts
  `state.json` and `terrain/` at the bucket root.
* One prefix per environment (e.g. `s3://wg-response-state/prod`,
  `.../shadow`). Two daemons sharing a prefix share terrain cache safely but
  will overwrite each other's `state.json` (last writer wins) — give each its
  own prefix.
* Region and credentials come from the standard AWS environment (App Runner
  instance role / `AWS_REGION`). There is no region parameter.
* Suggested CLI wiring (owned by `cli.py`, not by this module):

  ```python
  s3_uri = os.environ.get("WGRESP_STATE_S3") or None
  store  = StateStore(state_dir, s3_uri=s3_uri)
  store.probe_mirror()          # startup: RuntimeError if boto3 is missing
  terrain = LfpsTerrain(cache_dir=cache_dir, email=email, s3_uri=s3_uri)
  ```
  and surface `store.mirror_status()` in the startup banner / `/api/state`.

---

## 3. API

```python
persist.StateStore(dir_path, s3_uri=None, s3_client=None)

    .save(alert_records, notified_ledger, incumbent_plan, meta,
          incident_growth=None, now_s=None) -> str     # local path
    .load() -> dict | None                             # never raises
    .mirror_status() -> dict
    .probe_mirror() -> None                            # RuntimeError if unusable
    .path          -> Path                             # local state file
    .s3_key        -> str | None                       # "<prefix>/state.json"
    .s3_enabled    -> bool

terrain.LfpsTerrain(cache_dir=None, http_get=None, layer_list=..., email=None,
                    poll_interval_s=5.0, timeout_s=600.0, sleep_fn=time.sleep,
                    dep_probe=None, s3_uri=None, s3_client=None)

    .tile(bbox, cell_m) -> TerrainTile                 # unchanged signature
    .cache_path(bbox, cell_m) / .zip_cache_path(bbox)  # unchanged
    .s3_key(local_path) -> str | None                  # "<prefix>/terrain/<file>"
    .mirror_status() -> dict
    .s3_enabled -> bool
```

* `s3_client` injects any boto3-style S3 client (`put_object(Bucket=, Key=,
  Body=bytes)`, `get_object(Bucket=, Key=) -> {"Body": <.read()>}`). Tests
  inject an in-memory fake; **no test ever touches AWS**.
* `now_s` is the caller's clock, used only to stamp `mirror_status()`.
  `persist` still reads no wall clock of its own.
* `LfpsTerrain(s3_uri=...)` **without** `cache_dir` logs a warning and stays
  off — there is nothing local to mirror.
* `boto3` remains an **optional** dependency (`requirements.txt`), imported
  lazily on first S3 use, exactly like `clients.DynamoClientRegistry`. Its
  absence is a clear `RuntimeError` naming `pip install boto3` — raised only
  on the S3 path, i.e. only when a mirror was actually configured.
  `probe_mirror()` exists so the CLI can surface that at startup instead of
  discovering it in a log line an hour later.

### `mirror_status()`

```python
{"enabled": bool, "uri": str|None, "bucket": str|None, "prefix": str|None,
 "last_upload_ok": True|False|None,      # None = never attempted
 "last_upload_s": float|None,            # the now_s the CALLER passed
 "last_upload_key": str|None, "last_error": str|None,
 # StateStore only:
 "key": str|None,                        # "<prefix>/state.json"
 "restored_from_s3": bool,               # did THIS process restore from S3?
 "last_download": "hit"|"missing"|"error"|None}
```

A daemon that shows `enabled: true` with `last_upload_ok: false` is running
**without durable state**: the next restart will re-fire alerts. Alarm on it.

---

## 4. Failure semantics — loud, never fatal

The rule everywhere: **the local write is the authority; the mirror is a
convenience for the next container.** No S3 condition can crash the alert
loop or change what `save()`/`load()`/`tile()` return locally.

### `StateStore.save()`
1. Existing atomic local write, unchanged: `tmp` → `write` → `flush` →
   `os.fsync` → `os.replace`. (Unchanged on purpose — a power loss must never
   leave a torn state file.)
2. **Then** `put_object` of the same serialized bytes.
3. Upload failure ⇒ `ERROR` log naming the key and warning that a container
   restart will not find this state; `save()` still returns the local path and
   still counts as success. `mirror_status()["last_upload_ok"]` goes `False`.

   *(Detail: the object holds the serialized LF bytes; a Windows dev box's
   local file has CRLF. Same JSON document either way, and restores are
   written back in binary mode.)*

### `StateStore.load()` — never raises, in any of these cases

| condition | behaviour |
| --- | --- |
| local file present | S3 is **not** consulted — a live container's own write is newer than the object, and re-downloading could rewind the ladder/ledger |
| local absent, object absent | `INFO` "no mirrored state … first run for this prefix", start fresh |
| local absent, object present | downloaded to the local path with the same tmp+fsync+replace discipline, then the existing tolerant load runs; `restored_from_s3` → `True` |
| S3 error (AccessDenied, throttling, no bucket, no boto3, network) | `ERROR` naming the failure and `s3:GetObject`, **start fresh** |
| object is corrupt / wrong schema | it lands on disk and is rejected by the *existing* loud tolerant-load path (`ERROR` + start fresh); the next `save()` overwrites the bad object |
| local write of the download fails | `ERROR`, start fresh |

"Start fresh" always errs re-notify-ward, never silence-ward — a duplicate
alert beats a swallowed one (module discipline in `persist.py`).

### `LfpsTerrain.tile()` — order of operations

1. local `.wgtile` hit → return (offline, dep-free);
2. **mirrored `.wgtile`** → download, return (offline, and still **dep-free**:
   no GeoTIFF decode is involved, so the optional-dep probe does not run);
3. optional-dep probe (`tifffile`/`numpy`/`imagecodecs`) — fail fast before
   spending an LFPS job (ops review OPS-2);
4. local `.zip` hit → decode without re-submitting;
5. **mirrored `.zip`** → download, decode without re-submitting;
6. submit LFPS job → write the zip locally **and upload it** before decode →
   decode → write the `.wgtile` locally **and upload it**.

Failures: a missing object is an `INFO` note (first fetch for this tile); any
other S3 error is an `ERROR` and falls through to the live LFPS job; a
**corrupt mirrored `.wgtile`** is logged, deleted locally and re-fetched for
real (the re-upload repairs the object), so a bad object can never poison
every restart. A corrupt mirrored `.zip` behaves exactly like a corrupt local
zip (the decode raises `RuntimeError` for that cycle) — delete the object to
clear it.

---

## 5. AWS setup

### IAM — the task role needs, on that bucket only

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WgResponseDurableStateObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::wg-response-state/<prefix>/*"
    },
    {
      "Sid": "WgResponseDurableStateBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::wg-response-state",
      "Condition": {"StringLike": {"s3:prefix": ["<prefix>/*"]}}
    }
  ]
}
```

* `s3:GetObject` + `s3:PutObject` are what the code actually calls
  (`get_object` / `put_object`).
* `s3:ListBucket` is listed because without it S3 answers a *missing* object
  with **403 AccessDenied** instead of 404 `NoSuchKey` — which this code
  correctly treats as a real failure (loud `ERROR`, start fresh) rather than
  "first run". Granting it makes the first-run path quiet and correct, and
  operators need it for `aws s3 ls` anyway.
* No delete permission is granted or needed. Nothing in the daemon ever
  deletes a mirrored object.

### Bucket

* Same region as the service; Block Public Access ON; default SSE (SSE-S3 is
  enough — the state file holds client ids, incident ids and alert times).
* **Versioning ON** is recommended: it turns "a bad deploy wrote garbage
  state" into a one-command recovery.
* Lifecycle: expire `<prefix>/terrain/` objects after ~90 days (LANDFIRE
  layers change annually; a deleted tile just costs one LFPS job). Do **not**
  expire `<prefix>/state.json`.
* Cost is negligible: one small JSON PUT per cycle plus a few MB of terrain.

### Verifying a deploy

```bash
aws s3 ls s3://wg-response-state/<prefix>/                 # state.json present?
aws s3 ls s3://wg-response-state/<prefix>/terrain/ | head  # tiles accumulating?
```
Then force a restart and confirm in the logs: `persist: restored … bytes of
state from s3://…` and `terrain: pulled … bytes from s3://…` (and that the
first cycle is fast). `mirror_status()` should read
`{"enabled": true, "last_upload_ok": true, "restored_from_s3": true}`.

---

## 6. Tests

`tests/test_persist_s3.py`, `tests/test_terrain_s3.py` — stdlib only, no AWS,
no boto3, no botocore. The fake S3 client is an in-memory dict with
botocore-*shaped* errors (the production code duck-types `Error.Code` /
`HTTPStatusCode` precisely so botocore need not be imported). Covered:
the restart drill through S3 (container A saves, container B restores into an
empty dir and re-fires nothing), loud-but-successful upload failure, missing
/ broken / corrupt objects, local-file-wins, absent `boto3`, terrain
miss→download skipping the LFPS submit (with an `http_get` that fails the test
if called), terrain uploads, and the `s3_uri=None` no-change paths.

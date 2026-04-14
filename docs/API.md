# Bird Detection Catalog API

**Base URL (Tailscale Funnel):** `https://<pi-name>.<tailnet>.ts.net`
**Base URL (local):** `http://localhost:8000`

All API paths are prefixed with `/api/v1` unless noted otherwise. Responses are JSON unless the endpoint returns binary data (images, video).

CORS is restricted to `https://jlav.io` through the Nginx proxy.

---

## Table of Contents

- [Security](#security)
- [System](#system)
- [Dashboard & Analytics](#dashboard--analytics)
- [Detections](#detections)
- [Detection Frames & Video](#detection-frames--video)
- [Yard Life List](#yard-life-list)
- [Species](#species)
- [eBird Data](#ebird-data)
- [Search](#search)
- [Audit Log](#audit-log)
- [Live Stream](#live-stream)

---

## Security

The public API (via Tailscale Funnel) is **read-only**. All protections are enforced at the Nginx reverse proxy layer.

| Protection | Implementation |
|------------|---------------|
| **Write methods blocked** | `limit_except GET HEAD OPTIONS { deny all; }` -- POST/PUT/DELETE/PATCH return `403` |
| **CORS** | `Access-Control-Allow-Origin: https://jlav.io` -- only your site can make cross-origin requests |
| **Swagger/ReDoc hidden** | `/docs`, `/redoc`, `/openapi.json` return `404` through the proxy |
| **Metrics hidden** | `/metrics` returns `404` through the proxy |
| **Video rate limited** | 2 requests/minute per IP with burst of 3 (returns `429` when exceeded) |
| **Request body size** | `client_max_body_size 1k` on the API proxy -- effectively blocks upload attempts |
| **Frame upload limits** | Server-side: max 200 frames/batch, max ~1.5MB per frame |
| **Search wildcards** | ILIKE `%` and `_` characters are escaped to prevent full-table scans |

Internal services (inference worker, TorchServe) communicate directly on port 8000 over the Docker network and are not affected by these restrictions.

To access Swagger UI, `/metrics`, or POST endpoints, connect directly to `http://localhost:8000` on the Pi.

---

## System

### Health Check

```
GET /health
```

**Response** `200`

```json
{ "status": "ok" }
```

### Prometheus Metrics

```
GET /metrics
```

**Response** `200` — `text/plain` (Prometheus exposition format)

---

## Dashboard & Analytics

### Summary Statistics

```
GET /api/v1/analytics/summary
```

**Response** `200`

```json
{
  "total_detections": 847,
  "unique_species": 23,
  "average_confidence": 0.8724,
  "latest_detection": "2026-04-14T17:14:14.793000+00:00",
  "detections_today": 42
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_detections` | `int` | All-time detection count |
| `unique_species` | `int` | Distinct species IDs detected |
| `average_confidence` | `float` | Mean confidence across all detections |
| `latest_detection` | `string\|null` | ISO 8601 timestamp of most recent detection |
| `detections_today` | `int` | Detections since midnight (server time) |

---

## Detections

### List Detections

```
GET /api/v1/detections
```

**Query Parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `species_id` | `int` | — | Filter by species ID |
| `min_confidence` | `float` | — | Minimum confidence threshold |
| `start` | `datetime` | — | ISO 8601, detections on or after |
| `end` | `datetime` | — | ISO 8601, detections on or before |
| `page` | `int` | `1` | Page number (1-indexed) |
| `page_size` | `int` | `50` | Items per page (max 200) |

**Response** `200`

```json
{
  "items": [
    {
      "id": "2abac43b-...",
      "species_id": 42,
      "confidence": 0.95,
      "frame_s3_key": "",
      "frame_url": null,
      "source_camera": "birdcam-01",
      "detected_at": "2026-04-14T17:14:03.141000+00:00",
      "bounding_box": null,
      "extra_metadata": {
        "frame_id": "9e6c0953-...",
        "species_code": "blujay",
        "common_name": "Blue Jay",
        "was_rerouted": false,
        "is_notable": false,
        "top5": [
          { "species": "Blue Jay", "confidence": 0.95 },
          { "species": "Steller's Jay", "confidence": 0.03 }
        ]
      },
      "created_at": "2026-04-14T17:14:03.169000+00:00",
      "frame_count": 15
    }
  ],
  "total": 847,
  "page": 1,
  "page_size": 50
}
```

#### Detection Object

| Field | Type | Description |
|-------|------|-------------|
| `id` | `uuid` | Detection UUID |
| `species_id` | `int\|null` | FK to species table |
| `confidence` | `float` | eBird-adjusted confidence (0.0-1.0) |
| `frame_s3_key` | `string` | Legacy field (always `""`) |
| `frame_url` | `string\|null` | Legacy field (always `null`) |
| `source_camera` | `string` | Camera identifier |
| `detected_at` | `string` | ISO 8601 detection timestamp |
| `bounding_box` | `object\|null` | YOLO bounding box if available |
| `extra_metadata` | `object\|null` | See sub-fields below |
| `created_at` | `string` | Row creation timestamp |
| `frame_count` | `int\|null` | Number of recorded video frames |

#### `extra_metadata` Sub-fields

| Field | Type | Description |
|-------|------|-------------|
| `frame_id` | `string` | UUID of the triggering frame |
| `species_code` | `string` | eBird species code (e.g. `"blujay"`) |
| `common_name` | `string` | Common English name |
| `was_rerouted` | `bool` | Model's #1 pick was overridden by eBird |
| `is_notable` | `bool` | Rare species for the region |
| `top5` | `array` | Top 5 model predictions with `species` and `confidence` |

### Get Single Detection

```
GET /api/v1/detections/{detection_id}
```

**Response** `200` — Same shape as a single item from the list endpoint.

**Response** `404`

```json
{ "detail": "Detection not found" }
```

---

## Detection Frames & Video

Each detection may have a set of recorded frames captured during the detection cooldown period. Frames are JPEG images stored in the database.

### List Frames (metadata)

```
GET /api/v1/detections/{detection_id}/frames
```

**Response** `200`

```json
{
  "detection_id": "2abac43b-...",
  "total_frames": 15,
  "frames": [
    {
      "id": "f1a2b3c4-...",
      "detection_id": "2abac43b-...",
      "sequence_number": 0,
      "captured_at": "2026-04-14T17:14:03.200000+00:00",
      "has_bird": true,
      "frame_width": 640,
      "frame_height": 360
    },
    {
      "id": "d5e6f7a8-...",
      "detection_id": "2abac43b-...",
      "sequence_number": 1,
      "captured_at": "2026-04-14T17:14:03.533000+00:00",
      "has_bird": true,
      "frame_width": 640,
      "frame_height": 360
    }
  ]
}
```

#### Frame Object

| Field | Type | Description |
|-------|------|-------------|
| `id` | `uuid` | Frame UUID (use in image/video URLs) |
| `detection_id` | `uuid` | Parent detection |
| `sequence_number` | `int` | 0-indexed order within the recording session |
| `captured_at` | `string` | ISO 8601 capture timestamp |
| `has_bird` | `bool` | YOLO detected a bird in this specific frame |
| `frame_width` | `int\|null` | Pixel width |
| `frame_height` | `int\|null` | Pixel height |

### Get Frame Image (JPEG)

```
GET /api/v1/detections/{detection_id}/frames/{frame_id}/image
```

**Response** `200` — `image/jpeg` binary

Use directly as an `<img>` src:

```html
<img src="https://<pi>/api/v1/detections/{id}/frames/{fid}/image" />
```

**Response** `404`

```json
{ "detail": "Frame not found" }
```

### Get Detection Video (MP4)

```
GET /api/v1/detections/{detection_id}/video
```

Assembles all recorded frames into an H.264 MP4 video that plays natively in browsers.

**Query Parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fps` | `int` | `3` | Playback frame rate (1-30) |

**Response** `200` — `video/mp4` binary

Headers:
- `Content-Disposition: inline; filename="Blue_Jay_2abac43b.mp4"`
- `Cache-Control: public, max-age=3600`

Use directly as a `<video>` src:

```html
<video controls autoplay>
  <source src="https://<pi>/api/v1/detections/{id}/video?fps=3" type="video/mp4" />
</video>
```

**Response** `404`

```json
{ "detail": "No frames recorded for this detection" }
```

**Response** `500`

```json
{ "detail": "Video encoding failed" }
```

> **Note:** Video is generated on-demand by FFmpeg. The first request for a detection may take a few seconds depending on frame count. The `Cache-Control` header allows browser/proxy caching.

---

## Yard Life List

### Get Yard Life List

```
GET /api/v1/yard-list
```

Returns all species ever detected, ordered by most recently seen.

**Response** `200`

```json
[
  {
    "id": 1,
    "species_code": "blujay",
    "species_id": 42,
    "first_detected_at": "2026-04-14T17:12:46.355000+00:00",
    "last_detected_at": "2026-04-14T17:14:14.793000+00:00",
    "total_detections": 3,
    "best_confidence": 1.0,
    "best_frame_s3_key": "",
    "ebird_confirmed": true
  }
]
```

#### Yard Life List Entry

| Field | Type | Description |
|-------|------|-------------|
| `id` | `int` | Row ID |
| `species_code` | `string` | eBird species code |
| `species_id` | `int\|null` | FK to species table |
| `first_detected_at` | `string` | First time this species was seen |
| `last_detected_at` | `string` | Most recent sighting |
| `total_detections` | `int` | Cumulative detection count |
| `best_confidence` | `float\|null` | Highest confidence score ever recorded |
| `best_frame_s3_key` | `string\|null` | Legacy field |
| `ebird_confirmed` | `bool` | Species is on the eBird local list |

### Get Yard List Stats

```
GET /api/v1/yard-list/stats
```

**Response** `200`

```json
{
  "total_species": 23,
  "total_detections": 847,
  "ebird_confirmed_count": 21,
  "local_list_size": 312,
  "coverage_pct": 7.4,
  "latest_new_species": "yerwar",
  "latest_new_species_date": "2026-04-14T17:12:46.355000+00:00"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_species` | `int` | Unique species on yard list |
| `total_detections` | `int` | Sum of all detections across species |
| `ebird_confirmed_count` | `int` | Species confirmed against eBird local list |
| `local_list_size` | `int` | Total species on the eBird local list for the region |
| `coverage_pct` | `float` | `total_species / local_list_size * 100` |
| `latest_new_species` | `string\|null` | Species code of most recently added lifer |
| `latest_new_species_date` | `string\|null` | When it was first detected |

---

## Species

### List All Species

```
GET /api/v1/species
```

Returns all known species with detection counts, ordered by most-detected first.

**Response** `200`

```json
[
  {
    "id": 42,
    "cub_class_id": 42,
    "common_name": "Blue Jay",
    "scientific_name": "Cyanocitta cristata",
    "family": "Corvidae",
    "species_code": "blujay",
    "order": "Passeriformes",
    "detection_count": 156
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | `int` | Internal species ID |
| `cub_class_id` | `int` | CUB-200-2011 class ID |
| `common_name` | `string` | Common English name |
| `scientific_name` | `string\|null` | Latin binomial |
| `family` | `string\|null` | Taxonomic family |
| `species_code` | `string\|null` | eBird species code |
| `order` | `string\|null` | Taxonomic order |
| `detection_count` | `int` | Number of detections for this species |

### Get Single Species

```
GET /api/v1/species/{species_id}
```

**Response** `200` — Single species object (without `detection_count`).

### Species Detection Timeline

```
GET /api/v1/species/{species_id}/timeline
```

Daily detection frequency for a species.

**Response** `200`

```json
{
  "species": {
    "id": 42,
    "cub_class_id": 42,
    "common_name": "Blue Jay",
    "scientific_name": "Cyanocitta cristata",
    "family": "Corvidae",
    "species_code": "blujay",
    "order": "Passeriformes"
  },
  "timeline": [
    { "date": "2026-04-14", "count": 3, "avg_confidence": 0.9833 },
    { "date": "2026-04-15", "count": 7, "avg_confidence": 0.9142 }
  ]
}
```

### Species Migration Chart

```
GET /api/v1/species/{species_id}/migration
```

Detection timeline overlaid with eBird seasonal frequency data for migration visualization.

**Response** `200`

```json
{
  "species_id": 42,
  "species_code": "blujay",
  "common_name": "Blue Jay",
  "ebird_frequency": [
    { "week": 1, "frequency": 0.82 },
    { "week": 2, "frequency": 0.79 }
  ],
  "detections_by_week": [
    { "week": "2026-04-07 00:00:00+00:00", "count": 12, "avg_confidence": 0.91 }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ebird_frequency` | `array` | 48 weeks of eBird checklist frequency (0.0-1.0) |
| `ebird_frequency[].week` | `int` | Week number (1-48) |
| `ebird_frequency[].frequency` | `float` | Fraction of checklists reporting this species |
| `detections_by_week` | `array` | Your detections bucketed by calendar week |
| `detections_by_week[].week` | `string` | Start of the week (ISO 8601) |
| `detections_by_week[].count` | `int` | Detections that week |
| `detections_by_week[].avg_confidence` | `float` | Mean confidence that week |

---

## eBird Data

### Local Species List

```
GET /api/v1/ebird/local-species
```

All species on the eBird local list for the configured region, with current-week frequency.

**Response** `200`

```json
[
  {
    "species_code": "blujay",
    "common_name": "Blue Jay",
    "scientific_name": "Cyanocitta cristata",
    "last_observed": "2026-04-12",
    "observation_count": 542,
    "is_notable": false,
    "current_week_frequency": 0.82
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `species_code` | `string` | eBird species code |
| `common_name` | `string` | Common English name |
| `scientific_name` | `string\|null` | Latin binomial |
| `last_observed` | `string\|null` | Date of most recent eBird observation |
| `observation_count` | `int` | eBird observation count |
| `is_notable` | `bool` | Rare for the region |
| `current_week_frequency` | `float\|null` | Checklist frequency for the current week (0.0-1.0) |

### Notable Sightings

```
GET /api/v1/ebird/notable
```

**Query Parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | `int` | `50` | Max results (up to 200) |

**Response** `200`

```json
[
  {
    "id": 1,
    "species_code": "snoowl1",
    "common_name": "Snowy Owl",
    "observed_at": "2026-04-10T14:30:00+00:00",
    "lat": 43.05,
    "lng": -89.40,
    "location_name": "Pheasant Branch Conservancy",
    "how_many": 1
  }
]
```

### Hotspots

```
GET /api/v1/ebird/hotspots
```

Nearby birding hotspots, ordered by species count.

**Response** `200`

```json
[
  {
    "hotspot_id": "L123456",
    "name": "Pheasant Branch Conservancy",
    "lat": 43.05,
    "lng": -89.40,
    "latest_obs_date": "2026-04-13",
    "num_species": 187
  }
]
```

---

## Search

### Full-Text Search

```
GET /api/v1/search
```

Searches across detection validation notes and metadata (species names, codes, etc.).

**Query Parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | `string` | **required** | Search query (min 1 char) |
| `page` | `int` | `1` | Page number |
| `page_size` | `int` | `20` | Items per page (max 100) |

**Response** `200`

```json
{
  "total": 12,
  "items": [
    {
      "detection_id": "2abac43b-...",
      "species": "Blue Jay",
      "species_code": "blujay",
      "confidence": 0.95,
      "detected_at": "2026-04-14T17:14:03.141000+00:00",
      "ebird_validated": true,
      "validation_notes": "Accepted rank-1 candidate"
    }
  ],
  "page": 1,
  "page_size": 20
}
```

---

## Audit Log

### Detection Audit Trail

```
GET /api/v1/detections/{detection_id}/audit
```

Full decision-making trace for a single detection: what candidates were considered, why some were rejected, and which was accepted.

**Response** `200`

```json
{
  "id": "a1b2c3d4-...",
  "detection_id": "2abac43b-...",
  "frame_id": "9e6c0953-...",
  "created_at": "2026-04-14T17:14:03.141000+00:00",
  "model_name": "bird_classifier",
  "inference_latency_ms": 377.0,
  "candidates": [
    {
      "rank": 1,
      "species_code": "blujay",
      "common_name": "Blue Jay",
      "raw_confidence": 0.95,
      "on_local_list": true,
      "seasonal_frequency": 0.82,
      "adjusted_confidence": 0.97,
      "rejection_reason": null
    },
    {
      "rank": 2,
      "species_code": "stejay",
      "common_name": "Steller's Jay",
      "raw_confidence": 0.03,
      "on_local_list": false,
      "seasonal_frequency": null,
      "adjusted_confidence": 0.001,
      "rejection_reason": "Not on local species list"
    }
  ],
  "ebird_region": "US-WI",
  "ebird_week": 15,
  "local_list_size": 312,
  "accepted_rank": 1,
  "accepted_species_code": "blujay",
  "final_confidence": 0.97,
  "was_rerouted": false,
  "is_notable": false,
  "decision_time_ms": 12.5,
  "summary": "Accepted rank-1 candidate Blue Jay (97.0%)",
  "pipeline_version": "3.0.0"
}
```

#### Candidate Object

| Field | Type | Description |
|-------|------|-------------|
| `rank` | `int` | Model prediction rank (1 = highest confidence) |
| `species_code` | `string` | eBird species code |
| `common_name` | `string` | Common English name |
| `raw_confidence` | `float` | Model's raw confidence (before eBird adjustment) |
| `on_local_list` | `bool\|null` | Whether species is on the eBird local list |
| `seasonal_frequency` | `float\|null` | eBird checklist frequency for current week |
| `adjusted_confidence` | `float\|null` | Bayesian-adjusted confidence |
| `rejection_reason` | `string\|null` | Why this candidate was rejected (null if accepted) |

### List Rerouted Detections

```
GET /api/v1/audit/rerouted
```

Detections where the model's top-1 prediction was overridden by eBird validation.

**Query Parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | `int` | `1` | Page number |
| `page_size` | `int` | `50` | Items per page (max 200) |

**Response** `200` — Array of audit log objects (same shape as single audit above).

### List Rejected Detections

```
GET /api/v1/audit/rejected
```

Detections where no candidate passed validation (`accepted_rank == 0`).

Same parameters and response shape as rerouted.

### Audit Stats

```
GET /api/v1/audit/stats
```

**Response** `200`

```json
{
  "total_decisions": 847,
  "rerouted_count": 23,
  "reroute_rate": 0.0272,
  "rejected_count": 5,
  "rejection_rate": 0.0059,
  "avg_decision_time_ms": 11.42,
  "top_rejection_reasons": [],
  "top_reroute_pairs": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `total_decisions` | `int` | Total audit log entries |
| `rerouted_count` | `int` | Detections where #1 was overridden |
| `reroute_rate` | `float` | `rerouted_count / total_decisions` |
| `rejected_count` | `int` | Detections with no valid candidate |
| `rejection_rate` | `float` | `rejected_count / total_decisions` |
| `avg_decision_time_ms` | `float\|null` | Mean eBird validation time |

---

## Live Stream

The HLS stream is served directly by Nginx (not the catalog API).

```
GET /stream.m3u8
```

**Response** `200` — `application/vnd.apple.mpegurl`

Use with hls.js or native `<video>`:

```html
<video id="player" controls autoplay muted></video>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<script>
  const video = document.getElementById('player');
  if (Hls.isSupported()) {
    const hls = new Hls();
    hls.loadSource('https://<pi-name>.ts.net/stream.m3u8');
    hls.attachMedia(video);
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = 'https://<pi-name>.ts.net/stream.m3u8';
  }
</script>
```

---

## Common Patterns

### Pagination

All paginated endpoints use `page` (1-indexed) and `page_size` with a `total` count in the response.

```
GET /api/v1/detections?page=2&page_size=10
```

### Date Filtering

Pass ISO 8601 strings for `start` and `end`:

```
GET /api/v1/detections?start=2026-04-14T00:00:00Z&end=2026-04-14T23:59:59Z
```

### Error Responses

All errors return JSON:

```json
{ "detail": "Detection not found" }
```

| Status | Meaning |
|--------|---------|
| `400` | Bad request (invalid params) |
| `404` | Resource not found |
| `422` | Validation error (FastAPI details array) |
| `500` | Server error |

### Fetching a Detection with its Video

```javascript
// 1. Get the detection
const det = await fetch('/api/v1/detections/2abac43b-...').then(r => r.json());

// 2. Check if it has frames
if (det.frame_count > 0) {
  // 3. Use the video endpoint as a <video> src
  videoElement.src = `/api/v1/detections/${det.id}/video?fps=3`;
}
```

### Building a Species Card

```javascript
// Get yard list entry
const yardList = await fetch('/api/v1/yard-list').then(r => r.json());
const blueJay = yardList.find(e => e.species_code === 'blujay');

// Get recent detections for that species
const dets = await fetch(
  `/api/v1/detections?species_id=${blueJay.species_id}&page_size=5`
).then(r => r.json());

// For each detection, show the video or first frame
for (const d of dets.items) {
  if (d.frame_count > 0) {
    // Video clip
    const videoUrl = `/api/v1/detections/${d.id}/video?fps=3`;
    // Or get thumbnail (first frame with a bird)
    const frames = await fetch(`/api/v1/detections/${d.id}/frames`).then(r => r.json());
    const thumb = frames.frames.find(f => f.has_bird) || frames.frames[0];
    const imgUrl = `/api/v1/detections/${d.id}/frames/${thumb.id}/image`;
  }
}
```

---

## OpenAPI / Swagger

FastAPI auto-generates interactive docs. These are **blocked from public access** through the Nginx proxy and only available locally on the Pi:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI JSON:** `http://localhost:8000/openapi.json`

Accessing `/docs`, `/redoc`, or `/openapi.json` through the Tailscale Funnel URL will return `404`.

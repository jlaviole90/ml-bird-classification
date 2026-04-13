# Sightings Dashboard

Angular standalone component + Vercel serverless proxy for displaying bird sightings data on jlav.io.

## Integration into jlav.io

### 1. Copy files to the Angular project

```bash
# Copy the component and service
cp dashboard/sightings/sightings.component.ts jlav.io/src/app/containers/sightings/
cp dashboard/sightings/sightings.service.ts jlav.io/src/app/containers/sightings/

# Copy the Vercel proxy
cp dashboard/sightings/api/sightings.ts jlav.io/api/sightings.ts
```

### 2. Add route to `app.routes.ts`

```typescript
{
  path: 'birds/sightings',
  loadComponent: () =>
    import('./containers/sightings/sightings.component').then(
      (m) => m.SightingsComponent
    ),
}
```

### 3. Vercel configuration

In `vercel.json`, add a rewrite to route `/api/sightings/*` through the serverless function:

```json
{
  "rewrites": [
    { "source": "/api/sightings/:path*", "destination": "/api/sightings" }
  ]
}
```

### 4. Environment variables

Set `CATALOG_API_URL` in Vercel project settings pointing to the FastAPI backend.

## Dashboard Sections

- **Stats summary** — total species, detections, eBird coverage, confirmed count
- **Identification quality** — reroute rate, rejection rate, avg decision time
- **Yard life list** — all confirmed species with thumbnails, detection counts, confidence
- **Rare sighting alerts** — notable/rare birds from eBird cross-referenced with detections
- **Pipeline status** — whether the camera/pipeline is running, latest new species

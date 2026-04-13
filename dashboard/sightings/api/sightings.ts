/**
 * Vercel serverless proxy — forwards requests from the Angular frontend
 * to the FastAPI catalog backend, keeping the API URL private.
 *
 * Deployed as: /api/sightings/*
 * Backend URL from env: CATALOG_API_URL
 */

import type { VercelRequest, VercelResponse } from "@vercel/node";

const CATALOG_API_URL =
  process.env.CATALOG_API_URL || "http://localhost:8000";

export default async function handler(
  req: VercelRequest,
  res: VercelResponse,
) {
  const { method, query } = req;

  // Build the target path: /api/sightings/yard-list -> /api/v1/yard-list
  const rawPath = (query.path as string[] | undefined) || [];
  const subpath = rawPath.join("/");
  const targetPath = `/api/v1/${subpath}`;

  // Forward query params (excluding the catch-all 'path')
  const params = new URLSearchParams();
  for (const [key, val] of Object.entries(query)) {
    if (key === "path") continue;
    if (Array.isArray(val)) {
      val.forEach((v) => params.append(key, v));
    } else if (val !== undefined) {
      params.append(key, val as string);
    }
  }

  const url = `${CATALOG_API_URL}${targetPath}${
    params.toString() ? `?${params.toString()}` : ""
  }`;

  try {
    const upstream = await fetch(url, {
      method: method || "GET",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
    });

    const data = await upstream.json();

    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");
    res.status(upstream.status).json(data);
  } catch (err: any) {
    console.error("Proxy error:", err);
    res.status(502).json({ error: "Failed to reach catalog API", detail: err.message });
  }
}

/**
 * Angular service for fetching sightings data from the Vercel proxy.
 */

import { HttpClient } from "@angular/common/http";
import { Injectable } from "@angular/core";
import { Observable } from "rxjs";

export interface YardLifeEntry {
  id: number;
  species_code: string;
  species_id: number | null;
  first_detected_at: string;
  last_detected_at: string;
  total_detections: number;
  best_confidence: number | null;
  best_frame_s3_key: string | null;
  ebird_confirmed: boolean;
}

export interface YardListStats {
  total_species: number;
  total_detections: number;
  ebird_confirmed_count: number;
  local_list_size: number;
  coverage_pct: number;
  latest_new_species: string | null;
  latest_new_species_date: string | null;
}

export interface NotableSighting {
  id: number;
  species_code: string;
  common_name: string;
  observed_at: string;
  lat: number | null;
  lng: number | null;
  location_name: string | null;
  how_many: number | null;
}

export interface LocalSpecies {
  species_code: string;
  common_name: string;
  scientific_name: string | null;
  last_observed: string | null;
  observation_count: number;
  is_notable: boolean;
  current_week_frequency: number | null;
}

export interface AuditLogEntry {
  id: string;
  detection_id: string | null;
  frame_id: string;
  created_at: string;
  model_name: string;
  candidates: CandidateEval[];
  accepted_species_code: string | null;
  final_confidence: number | null;
  was_rerouted: boolean;
  is_notable: boolean;
  summary: string | null;
}

export interface CandidateEval {
  rank: number;
  species_code: string;
  common_name: string;
  raw_confidence: number;
  on_local_list: boolean | null;
  seasonal_frequency: number | null;
  adjusted_confidence: number | null;
  rejection_reason: string | null;
}

export interface AuditStats {
  total_decisions: number;
  rerouted_count: number;
  reroute_rate: number;
  rejected_count: number;
  rejection_rate: number;
  avg_decision_time_ms: number | null;
}

@Injectable({ providedIn: "root" })
export class SightingsService {
  private readonly baseUrl = "/api/sightings";

  constructor(private http: HttpClient) {}

  getYardList(): Observable<YardLifeEntry[]> {
    return this.http.get<YardLifeEntry[]>(`${this.baseUrl}/yard-list`);
  }

  getYardListStats(): Observable<YardListStats> {
    return this.http.get<YardListStats>(`${this.baseUrl}/yard-list/stats`);
  }

  getNotableSightings(limit = 50): Observable<NotableSighting[]> {
    return this.http.get<NotableSighting[]>(
      `${this.baseUrl}/ebird/notable`,
      { params: { limit: limit.toString() } },
    );
  }

  getLocalSpecies(): Observable<LocalSpecies[]> {
    return this.http.get<LocalSpecies[]>(
      `${this.baseUrl}/ebird/local-species`,
    );
  }

  getDetectionAudit(detectionId: string): Observable<AuditLogEntry> {
    return this.http.get<AuditLogEntry>(
      `${this.baseUrl}/detections/${detectionId}/audit`,
    );
  }

  getAuditStats(): Observable<AuditStats> {
    return this.http.get<AuditStats>(`${this.baseUrl}/audit/stats`);
  }

  getSpeciesMigration(
    speciesId: number,
  ): Observable<{
    species_code: string;
    common_name: string;
    ebird_frequency: { week: number; frequency: number }[];
    detections_by_week: {
      week: string;
      count: number;
      avg_confidence: number;
    }[];
  }> {
    return this.http.get<any>(
      `${this.baseUrl}/species/${speciesId}/migration`,
    );
  }
}

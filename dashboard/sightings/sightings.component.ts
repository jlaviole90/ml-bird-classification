/**
 * Standalone Angular component — Sightings Dashboard.
 *
 * Sections: Yard life list, recent activity, frequency chart,
 * rare sighting alerts, stats summary, live status.
 */

import { CommonModule, DatePipe, DecimalPipe } from "@angular/common";
import { Component, OnInit } from "@angular/core";
import { HttpClientModule } from "@angular/common/http";

import {
  SightingsService,
  YardLifeEntry,
  YardListStats,
  NotableSighting,
  AuditStats,
} from "./sightings.service";

@Component({
  selector: "app-sightings",
  standalone: true,
  imports: [CommonModule, HttpClientModule, DatePipe, DecimalPipe],
  template: `
    <div class="sightings-dashboard">
      <header class="dashboard-header">
        <h1>Bird Sightings Dashboard</h1>
        <p class="subtitle">
          Powered by ML classification + eBird validation
        </p>
      </header>

      <!-- Stats Summary -->
      <section class="stats-grid" *ngIf="stats">
        <div class="stat-card">
          <span class="stat-value">{{ stats.total_species }}</span>
          <span class="stat-label">Species Detected</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ stats.total_detections | number }}</span>
          <span class="stat-label">Total Detections</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ stats.coverage_pct }}%</span>
          <span class="stat-label">Local Species Coverage</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ stats.ebird_confirmed_count }}</span>
          <span class="stat-label">eBird Confirmed</span>
        </div>
      </section>

      <!-- Audit Stats -->
      <section class="audit-stats" *ngIf="auditStats">
        <h2>Identification Quality</h2>
        <div class="stats-grid">
          <div class="stat-card">
            <span class="stat-value">{{ auditStats.total_decisions | number }}</span>
            <span class="stat-label">Decisions Made</span>
          </div>
          <div class="stat-card">
            <span class="stat-value">{{ (auditStats.reroute_rate * 100) | number:'1.1-1' }}%</span>
            <span class="stat-label">Reroute Rate</span>
          </div>
          <div class="stat-card">
            <span class="stat-value">{{ (auditStats.rejection_rate * 100) | number:'1.1-1' }}%</span>
            <span class="stat-label">Rejection Rate</span>
          </div>
          <div class="stat-card" *ngIf="auditStats.avg_decision_time_ms !== null">
            <span class="stat-value">{{ auditStats.avg_decision_time_ms | number:'1.1-1' }}ms</span>
            <span class="stat-label">Avg Decision Time</span>
          </div>
        </div>
      </section>

      <!-- Yard Life List -->
      <section class="yard-list">
        <h2>Yard Life List</h2>
        <div class="species-grid">
          <div
            class="species-card"
            *ngFor="let entry of yardList"
            [class.notable]="entry.ebird_confirmed"
          >
            <div class="species-thumbnail" *ngIf="entry.best_frame_s3_key">
              <img
                [src]="'/api/sightings/frames/' + entry.best_frame_s3_key"
                [alt]="entry.species_code"
                loading="lazy"
              />
            </div>
            <div class="species-info">
              <h3>{{ entry.species_code }}</h3>
              <p class="detection-count">
                {{ entry.total_detections }} detection{{
                  entry.total_detections !== 1 ? "s" : ""
                }}
              </p>
              <p class="date-range">
                First: {{ entry.first_detected_at | date : "mediumDate" }}
              </p>
              <p class="date-range">
                Last: {{ entry.last_detected_at | date : "mediumDate" }}
              </p>
              <p class="confidence" *ngIf="entry.best_confidence">
                Best confidence: {{ entry.best_confidence | number : "1.1-2" }}
              </p>
              <span class="badge confirmed" *ngIf="entry.ebird_confirmed"
                >eBird Confirmed</span
              >
            </div>
          </div>
        </div>
      </section>

      <!-- Notable Sightings -->
      <section class="notable-sightings" *ngIf="notableSightings.length">
        <h2>Rare Sighting Alerts</h2>
        <div class="notable-list">
          <div class="notable-card" *ngFor="let sighting of notableSightings">
            <div class="notable-header">
              <span class="notable-species">{{ sighting.common_name }}</span>
              <span class="notable-badge">RARE</span>
            </div>
            <p>{{ sighting.observed_at | date : "medium" }}</p>
            <p *ngIf="sighting.location_name">
              {{ sighting.location_name }}
            </p>
            <p *ngIf="sighting.how_many">
              Count: {{ sighting.how_many }}
            </p>
          </div>
        </div>
      </section>

      <!-- Pipeline Status -->
      <section class="pipeline-status">
        <h2>Pipeline Status</h2>
        <div class="status-indicator">
          <span class="status-dot" [class.active]="pipelineActive"></span>
          <span>{{ pipelineActive ? "Camera Active" : "Camera Offline" }}</span>
        </div>
        <p class="last-update" *ngIf="stats?.latest_new_species_date">
          Latest new species:
          {{ stats!.latest_new_species }} on
          {{ stats!.latest_new_species_date | date : "mediumDate" }}
        </p>
      </section>
    </div>
  `,
  styles: [
    `
      .sightings-dashboard {
        max-width: 1200px;
        margin: 0 auto;
        padding: 2rem;
        font-family: system-ui, -apple-system, sans-serif;
        color: #1a1a2e;
      }

      .dashboard-header {
        text-align: center;
        margin-bottom: 2rem;
      }

      .dashboard-header h1 {
        font-size: 2rem;
        font-weight: 700;
        margin: 0 0 0.5rem;
      }

      .subtitle {
        color: #6b7280;
        font-size: 0.95rem;
      }

      h2 {
        font-size: 1.4rem;
        margin: 2rem 0 1rem;
        border-bottom: 2px solid #e5e7eb;
        padding-bottom: 0.5rem;
      }

      .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 1rem;
        margin-bottom: 1.5rem;
      }

      .stat-card {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 1.25rem;
        text-align: center;
      }

      .stat-value {
        display: block;
        font-size: 2rem;
        font-weight: 700;
        color: #059669;
      }

      .stat-label {
        display: block;
        font-size: 0.85rem;
        color: #6b7280;
        margin-top: 0.25rem;
      }

      .species-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 1rem;
      }

      .species-card {
        background: #fff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        overflow: hidden;
        transition: box-shadow 0.2s;
      }

      .species-card:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
      }

      .species-card.notable {
        border-color: #059669;
      }

      .species-thumbnail img {
        width: 100%;
        height: 180px;
        object-fit: cover;
      }

      .species-info {
        padding: 1rem;
      }

      .species-info h3 {
        margin: 0 0 0.25rem;
        font-size: 1.1rem;
      }

      .species-info p {
        margin: 0.15rem 0;
        font-size: 0.85rem;
        color: #4b5563;
      }

      .badge {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-top: 0.5rem;
      }

      .badge.confirmed {
        background: #d1fae5;
        color: #059669;
      }

      .notable-list {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
        gap: 1rem;
      }

      .notable-card {
        background: #fffbeb;
        border: 1px solid #f59e0b;
        border-radius: 12px;
        padding: 1rem;
      }

      .notable-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
      }

      .notable-species {
        font-weight: 700;
        font-size: 1.05rem;
      }

      .notable-badge {
        background: #f59e0b;
        color: #fff;
        padding: 0.15rem 0.5rem;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 700;
      }

      .notable-card p {
        margin: 0.2rem 0;
        font-size: 0.85rem;
        color: #6b7280;
      }

      .pipeline-status {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 1.5rem;
        margin-top: 2rem;
      }

      .status-indicator {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-weight: 600;
      }

      .status-dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #d1d5db;
      }

      .status-dot.active {
        background: #10b981;
        box-shadow: 0 0 6px #10b981;
      }

      .last-update {
        margin-top: 0.75rem;
        font-size: 0.85rem;
        color: #6b7280;
      }
    `,
  ],
})
export class SightingsComponent implements OnInit {
  yardList: YardLifeEntry[] = [];
  stats: YardListStats | null = null;
  auditStats: AuditStats | null = null;
  notableSightings: NotableSighting[] = [];
  pipelineActive = false;

  constructor(private sightingsService: SightingsService) {}

  ngOnInit(): void {
    this.sightingsService.getYardList().subscribe({
      next: (data) => (this.yardList = data),
      error: (err) => console.error("Failed to load yard list:", err),
    });

    this.sightingsService.getYardListStats().subscribe({
      next: (data) => {
        this.stats = data;
        this.pipelineActive = data.total_detections > 0;
      },
      error: (err) => console.error("Failed to load stats:", err),
    });

    this.sightingsService.getAuditStats().subscribe({
      next: (data) => (this.auditStats = data),
      error: (err) => console.error("Failed to load audit stats:", err),
    });

    this.sightingsService.getNotableSightings().subscribe({
      next: (data) => (this.notableSightings = data),
      error: (err) => console.error("Failed to load notable sightings:", err),
    });
  }
}

-- Migration 010: Add San Luis Obispo Randonneurs club
-- Run this before deploying the SLO brevet scraper.

INSERT INTO club (code, name, region)
VALUES ('SLO', 'San Luis Obispo Randonneurs', 'San Luis Obispo')
ON CONFLICT (code) DO NOTHING;

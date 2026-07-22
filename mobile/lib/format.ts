/**
 * mobile/lib/format.ts — small shared formatting/derivation helpers.
 *
 * Extracted so the ride screens (plan, weather, [id]) share one implementation
 * instead of copy-pasting near-identical copies that can silently drift.
 */
import { colors } from './theme';

/**
 * Map a wind label ("headwind"/"tailwind"/other) to a status color.
 * Returns the muted gray when the label is missing.
 */
export function windColor(label?: string | null): string {
  if (!label) return colors.textMuted;
  if (label.includes('headwind')) return colors.red;
  if (label.includes('tailwind')) return colors.green;
  return colors.blue;
}

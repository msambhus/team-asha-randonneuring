/**
 * mobile/lib/theme.ts — the app's shared design tokens.
 *
 * Before this module the palette lived as loose hex literals repeated across
 * screens (two clashing navies, 30+ duplicated colors). Import from here instead
 * so a color/spacing change happens in one place. Values match the web app's
 * CSS-variable palette (static/style.css) so the two surfaces stay in sync.
 */

export const colors = {
  // Brand
  navy: '#1a365d', // primary — headers, links, key figures
  navyDark: '#2a4a7f',

  // Semantic / status
  red: '#dc2626', // headwind, errors, danger
  green: '#16a34a', // tailwind, success, on-plan
  blue: '#2563eb', // neutral/info, crosswind
  amber: '#d97706', // rest stops, caution

  // Text
  text: '#1f2937', // primary body text
  textMuted: '#6b7280', // secondary text — contrast-safe on white (>=4.5:1)
  placeholder: '#9ca3af', // input placeholders / decorative dots ONLY (fails AA for body text)

  // Surfaces
  bg: '#f7fafc',
  cardBg: '#ffffff',
  border: '#e5e7eb',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const;

export const radius = {
  sm: 6,
  md: 10,
  lg: 14,
} as const;

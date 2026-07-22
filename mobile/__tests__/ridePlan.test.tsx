/**
 * mobile/__tests__/ridePlan.test.tsx — the ride plan table shows ETA in the main row
 * (replacing the old "ft" elevation column), and elevation moves to the tap-to-expand
 * detail as "climb". Also covers the PR #535 additions: the gradient elevation profile
 * renders when the server sends a profile, and the inline pace selector swaps the
 * itinerary on pick. useRidePlan + expo-router params are mocked, so no network.
 *
 * ⚠️ EAS-ONLY VERIFICATION: these render/interaction tests prove the components mount
 * and the pace tap swaps the visible rows — they do NOT prove the SVG pixel output.
 * The gradient elevation profile + overlay dots require a manual EAS build / Expo
 * simulator to verify visually. The harness Playwright step verifies only the backend
 * JSON contract (tests/test_api_ride_plan_elevation.py).
 */
import React from 'react';
import { render, fireEvent, screen } from '@testing-library/react-native';
import RidePlanScreen from '../app/ride/plan';
import * as useRidePlanHook from '../hooks/useRidePlan';
import type {
  ElevationProfileAvailable, PaceStop, PlanStop, RidePlanAvailable,
} from '../lib/types';

jest.mock('expo-router', () => ({
  useLocalSearchParams: () => ({ id: '42' }),
  useFocusEffect: jest.fn(),   // useAllowRotation() — no-op in the render test
}));
// Cut the auth import chain (SessionContext -> lib/auth -> native google-signin).
jest.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ token: 'tok-1', signOut: jest.fn() }),
}));
// react-native-svg has no jest-native impl; render its primitives as plain Views.
jest.mock('react-native-svg', () => {
  const R = require('react');
  const { View } = require('react-native');
  const C = (props: any) => R.createElement(View, props, props.children);
  return { __esModule: true, default: C, Svg: C, Circle: C, Path: C, Text: C };
});

const STOP: PlanStop = {
  stop_order: 1, location: 'San Rafael', stop_type: 'control', stop_name: null, notes: null,
  distance_mi: 60, seg_dist_mi: 60, elevation_gain_ft: 2400, ft_per_mi: 40,
  segment_time_min: 240, stop_duration_min: 0, cum_time_min: 240, arrival_time_min: 240,
  eta: '12:45 PM', time_bank_min: 960,
};

const DATA: RidePlanAvailable = {
  available: true,
  plan: {
    name: 'SCR 600K', slug: 'scr-600k', total_distance_mi: 120, total_elevation_ft: 9000,
    distance_km: 600, cutoff_hours: 40, start_time: '06:00', overall_ft_per_mile: 50,
  },
  has_custom: false, using_custom: false, custom_name: null, ride_date: '2026-07-04', stops: [STOP],
};

// ── PR #535 fixtures: a warmed profile + a pace map with a distinct Push itinerary ──
const PROFILE: ElevationProfileAvailable = {
  available: true, width: 1000, height: 200,
  plot: { x: 44, y: 12, w: 944, h: 166 }, total_mi: 120, min_ft: 60, max_ft: 900,
  area_path: 'M44 178L500 40L988 120L988 178Z',
  segments: [
    { d: 'M44 178L500 40', color: '#22c55e', grade: 2 },
    { d: 'M500 40L988 120', color: '#ef4444', grade: 9 },
  ],
  points: [[44, 178], [500, 40], [988, 120]],
  x_ticks: [{ x: 44, label: '0' }, { x: 988, label: '120' }],
  y_ticks: [{ y: 178, label: '60' }, { y: 40, label: '900' }],
  legend: [{ color: '#3b82f6', label: 'descent' }, { color: '#22c55e', label: '0–3%' }],
  markers: [],
};

function paceStop(over: Partial<PaceStop>): PaceStop {
  return {
    i: 0, type: 'control', name: 'Stop', cumul_mi: 0, eta: '06:00', elapsed: '0h00',
    bank: '+0:00', bank_min: 0, is_key: false, seg_mi: 0, seg_time_min: 0, break_min: 0,
    is_halt: false, fpm: 0, seg_speed: 0, seg_speed_known: false, headwind_mph: 0,
    wind_label: '', wind_arrow_deg: 0, wind_known: false, tough_class: '', tough_known: false,
    ...over,
  };
}

const WITH_PACE: RidePlanAvailable = {
  ...DATA,
  elevation_profile: PROFILE,
  pace_stops_map: {
    comfort: [paceStop({ i: 0, name: 'San Rafael', cumul_mi: 60, eta: '13:30' })],
    standard: [paceStop({ i: 0, name: 'San Rafael', cumul_mi: 60, eta: '12:45' })],
    // A Push-only control name so a swap to Push is observable in the table.
    push: [paceStop({ i: 0, name: 'Petaluma Sprint', cumul_mi: 60, eta: '12:05' })],
  },
  pace_cards_meta: [
    { id: 'comfort', name: 'Comfort', color: '#16a34a', summary: '', total: '40:00', sleep: '',
      has_sleep: false, bank: '+0:00', bank_good: true, risk: '', recommended: false },
    { id: 'standard', name: 'Standard', color: '#1a365d', summary: '', total: '38:00', sleep: '',
      has_sleep: false, bank: '+2:00', bank_good: true, risk: '', recommended: true },
    { id: 'push', name: 'Push', color: '#dc2626', summary: '', total: '36:00', sleep: '',
      has_sleep: false, bank: '+4:00', bank_good: true, risk: '', recommended: false },
  ],
};

function mockPlan(data: RidePlanAvailable = DATA) {
  jest.spyOn(useRidePlanHook, 'useRidePlan').mockReturnValue({
    data, isLoading: false, isError: false, refetch: jest.fn(),
  } as never);
}

describe('RidePlanScreen table', () => {
  afterEach(() => jest.restoreAllMocks());

  it('shows an ETA column header and the stop ETA in the main row', () => {
    mockPlan();
    render(<RidePlanScreen />);
    expect(screen.getByText('eta')).toBeTruthy();        // header
    expect(screen.queryByText('ft')).toBeNull();         // old elevation header is gone
    expect(screen.getByText('12:45 PM')).toBeTruthy();   // ETA in the collapsed main row
  });

  it('moves total elevation into the tap-to-expand detail as "climb"', () => {
    mockPlan();
    render(<RidePlanScreen />);
    // Elevation is not shown in the collapsed row.
    expect(screen.queryByText('2,400 ft')).toBeNull();
    // Expand the stop, then the climb detail appears.
    fireEvent.press(screen.getByText('San Rafael'));
    expect(screen.getByText('climb')).toBeTruthy();
    expect(screen.getByText('2,400 ft')).toBeTruthy();
  });
});

describe('RidePlanScreen elevation profile + pace selector (PR #535)', () => {
  afterEach(() => jest.restoreAllMocks());

  it('renders the elevation profile with its gradient legend when the server sends one', () => {
    mockPlan(WITH_PACE);
    render(<RidePlanScreen />);
    expect(screen.getByText('Elevation')).toBeTruthy();
    expect(screen.getByText('descent')).toBeTruthy();   // gradient legend label
    expect(screen.getByText('0–3%')).toBeTruthy();
  });

  it('renders the inline Comfort/Standard/Push selector', () => {
    mockPlan(WITH_PACE);
    render(<RidePlanScreen />);
    expect(screen.getByText('Choose your pace')).toBeTruthy();
    expect(screen.getByText('Comfort')).toBeTruthy();
    expect(screen.getByText('Standard')).toBeTruthy();
    expect(screen.getByText('Push')).toBeTruthy();
  });

  it('swaps the visible itinerary to the picked pace, client-side (no refetch)', () => {
    mockPlan(WITH_PACE);
    render(<RidePlanScreen />);
    // Base plan is shown first.
    expect(screen.getByText('San Rafael')).toBeTruthy();
    expect(screen.queryByText('Petaluma Sprint')).toBeNull();
    // Tap Push → the Push stop list replaces the itinerary; no useRidePlan refetch.
    fireEvent.press(screen.getByText('Push'));
    expect(screen.getByText('Petaluma Sprint')).toBeTruthy();
    expect(screen.queryByText('San Rafael')).toBeNull();
  });

  it('does not render the profile or selector when the server omits them (old backend)', () => {
    mockPlan(DATA);   // no elevation_profile / pace_stops_map
    render(<RidePlanScreen />);
    expect(screen.queryByText('Elevation')).toBeNull();
    expect(screen.queryByText('Choose your pace')).toBeNull();
  });
});

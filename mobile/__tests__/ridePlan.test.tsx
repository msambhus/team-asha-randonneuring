/**
 * mobile/__tests__/ridePlan.test.tsx — the ride plan table shows ETA in the main row
 * (replacing the old "ft" elevation column), and elevation moves to the tap-to-expand
 * detail as "climb". useRidePlan + expo-router params are mocked, so no network.
 */
import React from 'react';
import { render, fireEvent, screen } from '@testing-library/react-native';
import RidePlanScreen from '../app/ride/plan';
import * as useRidePlanHook from '../hooks/useRidePlan';
import type { PlanStop, RidePlanAvailable } from '../lib/types';

jest.mock('expo-router', () => ({ useLocalSearchParams: () => ({ id: '42' }) }));
// Cut the auth import chain (SessionContext -> lib/auth -> native google-signin).
jest.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ token: 'tok-1', signOut: jest.fn() }),
}));

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

function mockPlan() {
  jest.spyOn(useRidePlanHook, 'useRidePlan').mockReturnValue({
    data: DATA, isLoading: false, isError: false, refetch: jest.fn(),
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

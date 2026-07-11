/**
 * mobile/__tests__/rideLive.test.tsx — the RideLiveScreen wires the widened hook
 * output into the rider cards + the route-ahead charts. useLivePositions is mocked
 * at the hook boundary to return { positions, chart_data } (the same shape the real
 * hook now produces), so this proves the SCREEN renders the charts and the new
 * telemetry cells. (The real hook's chart_data preservation is proven separately in
 * useLivePositions.test.tsx — a mocked hook can't observe the real `select`.)
 */
import React from 'react';
import { render, screen } from '@testing-library/react-native';
import RideLiveScreen from '../app/ride/[id]';
import * as useLivePositionsHook from '../hooks/useLivePositions';
import type { LivePosition, LiveChartData } from '../lib/types';

jest.mock('expo-router', () => ({
  Stack: { Screen: () => null },
  useLocalSearchParams: () => ({ id: '42' }),
  useRouter: () => ({ push: jest.fn() }),
}));
jest.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ token: 'tok-1', signOut: jest.fn() }),
}));
jest.mock('@expo/vector-icons', () => ({ Feather: () => null }));
jest.mock('react-native-maps', () => {
  const React = require('react');
  const { View } = require('react-native');
  const Mock = (props: any) => React.createElement(View, props, props.children);
  return { __esModule: true, default: Mock, Marker: Mock, Polyline: Mock };
});
jest.mock('react-native-svg', () => {
  const React = require('react');
  const { View } = require('react-native');
  const C = (props: any) => React.createElement(View, props, props.children);
  return { __esModule: true, default: C, Svg: C, Circle: C, Line: C, Path: C, Polyline: C, Text: C };
});
jest.mock('../hooks/useRideRoute', () => ({ useRideRoute: () => ({ data: null, isLoading: false }) }));
jest.mock('../hooks/useSharing', () => ({ useSharing: () => ({ enabled: false }) }));
jest.mock('../location/backgroundLocation', () => ({
  isSharing: jest.fn(async () => false),
  startSharing: jest.fn(async () => null),
  stopSharing: jest.fn(async () => undefined),
}));

const CHART: LiveChartData = {
  labels: [0, 5, 10], elevation_ft: [100, 400, 250],
  headwind_mph: [6, -3, 8], temperature_f: [58, 64, 61],
};

function rider(over: Partial<LivePosition>): LivePosition {
  return {
    rider_id: 7, name: 'Asha Rider', lat: 37, lng: -122, status: 'GOING', color: '#16a34a',
    plan_color: '#16a34a', recorded_at: '2026-06-23T14:00:00Z', minutes_ago: 1, stale: false,
    source: 'garmin', trail: null,
    telemetry: {
      on_route: true,
      now: { speed_mph: 12, activity: 'cycling', elapsed_min: 60, moving_min: 55, stopped_min: 5,
             heart_rate: null, power: null, cadence: null, distance_mi: 5 },
      remaining: null,
      next_control: { name: 'Control 1', type: 'control', distance_mi: 10, dist_to_go_mi: 5,
                      arrival_time_min: 90, eta_iso: '2026-06-23T14:30:00Z', eta_label: '2:30 PM',
                      required_mph: 10, behind: false },
      plan: { delta_min: 12, banked_min: 12, status: 'ahead' },
      time_banked_cutoff_min: 45,
      time_banked_plan_min: 12,
      detailed_after_ride: true,
    },
    ...over,
  };
}

function mockPositions(positions: LivePosition[], chart_data: LiveChartData | null) {
  jest.spyOn(useLivePositionsHook, 'useLivePositions').mockReturnValue({
    data: { positions, chart_data }, isLoading: false,
  } as never);
}

describe('RideLiveScreen', () => {
  afterEach(() => jest.restoreAllMocks());

  it('renders the route-ahead charts and the new telemetry cells', () => {
    mockPositions([rider({})], CHART);
    render(<RideLiveScreen />);

    // Charts (WeatherChart titles carry a unit suffix, so match loosely).
    expect(screen.getByText(/Route ahead/)).toBeTruthy();
    expect(screen.getByText(/Elevation/)).toBeTruthy();
    expect(screen.getByText(/Headwind/)).toBeTruthy();
    expect(screen.getByText(/Temperature/)).toBeTruthy();

    // New telemetry cells on the rider card.
    expect(screen.getByText('ETA (arrival)')).toBeTruthy();
    expect(screen.getByText('2:30 PM')).toBeTruthy();
    expect(screen.getByText('req speed')).toBeTruthy();
    expect(screen.getByText('10 mph')).toBeTruthy();
    expect(screen.getByText('banked (cutoff)')).toBeTruthy();
    expect(screen.getByText('banked (plan)')).toBeTruthy();
    expect(screen.getByText('+45m')).toBeTruthy();     // cutoff margin, signed
  });

  it('shows an em-dash for required speed when the rider is behind', () => {
    mockPositions(
      [rider({ telemetry: { ...rider({}).telemetry!,
        next_control: { name: 'Control 1', type: 'control', distance_mi: 10, dist_to_go_mi: 5,
          arrival_time_min: 90, eta_iso: null, eta_label: '2:30 PM',
          required_mph: null, behind: true } } })],
      CHART);
    render(<RideLiveScreen />);
    expect(screen.getByText('req speed')).toBeTruthy();
    // required_mph null → em-dash rather than a negative/absent value.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('omits the charts block when the backend sends no chart_data', () => {
    mockPositions([rider({})], null);
    render(<RideLiveScreen />);
    expect(screen.queryByText(/Route ahead/)).toBeNull();
    // The rider card still renders.
    expect(screen.getByText('ETA (arrival)')).toBeTruthy();
  });
});

/**
 * mobile/__tests__/rideLive.test.tsx — the RideLiveScreen wires the widened hook
 * output into the rider cards + the route-ahead charts. useLivePositions is mocked
 * at the hook boundary to return { positions, chart_data } (the same shape the real
 * hook now produces), so this proves the SCREEN renders the charts and the new
 * telemetry cells. (The real hook's chart_data preservation is proven separately in
 * useLivePositions.test.tsx — a mocked hook can't observe the real `select`.)
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react-native';
import RideLiveScreen from '../app/ride/[id]';
import * as useLivePositionsHook from '../hooks/useLivePositions';
import type { LivePosition, LiveChartData, LivePlanOption, LivePlanSnapshot, UpcomingControl } from '../lib/types';

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
  // MapView forwards a ref exposing the imperative methods the screen calls
  // (fitToCoordinates in the auto-frame effect); a plain functional mock has no
  // ref, so mapRef.current.fitToCoordinates would be undefined.
  const MapView = React.forwardRef((props: any, ref: any) => {
    React.useImperativeHandle(ref, () => ({
      fitToCoordinates: () => {},
      animateToRegion: () => {},
    }));
    return React.createElement(View, props, props.children);
  });
  const Mock = (props: any) => React.createElement(View, props, props.children);
  return { __esModule: true, default: MapView, Marker: Mock, Polyline: Mock };
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
             current_stop_min: 3, stopped_ride_day_min: 4, active_day: 2,
             stop_events: [{ distance_mi: 4.2, duration_min: 4, day_number: 2,
               start_label: '1:00 PM', end_label: '1:04 PM' }],
             heart_rate: null, power: null, cadence: null, distance_mi: 5, ascent_done_ft: 2345 },
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

const PLANS: LivePlanOption[] = [
  { id: 'base', name: 'Base plan', owner: null, is_custom: false },
  { id: 11, name: 'Fast', owner: 'Alice', is_custom: true },
  { id: 'own', name: "Each rider's own plan", owner: null, is_custom: false },
];
const UPCOMING: UpcomingControl[] = [
  { name: 'Control 1, CA', type: 'control', distance_mi: 10, arrival_time_min: 90,
    eta_iso: null, eta_label: '9:30 AM' },
];

const PLAN_SNAPSHOT: LivePlanSnapshot = {
  name: 'Coulee Challenge', slug: 'coulee-challenge', active_day: 2, is_current_day: true,
  day_distance_mi: 182, day_elevation_ft: 8000, day_controls: 6,
  day_moving_min: 900, day_stopped_min: 300, day_elapsed_min: 1200,
  day_time_bank_min: 75,
  day_stops: [{ name: 'West Salem', distance_mi: 274, eta: '8:10 AM',
    eta_event_zone: 'CT', eta_pacific: '6:10 AM', show_pacific: true,
    break_min: 20, type: 'control', time_bank_min: 90 }],
};

type Extra = { plans?: LivePlanOption[]; selected_plan_id?: number | 'base' | 'own' | null; upcoming_controls?: UpcomingControl[]; plan_snapshot?: LivePlanSnapshot | null };

function mockPositions(positions: LivePosition[], chart_data: LiveChartData | null, extra: Extra = {}) {
  jest.spyOn(useLivePositionsHook, 'useLivePositions').mockReturnValue({
    data: { positions, chart_data, plans: [], selected_plan_id: null, upcoming_controls: [], plan_snapshot: null, ...extra },
    isLoading: false,
  } as never);
}

describe('RideLiveScreen', () => {
  afterEach(() => jest.restoreAllMocks());

  it('renders the route-ahead charts and the new telemetry cells', () => {
    mockPositions([rider({})], CHART);
    render(<RideLiveScreen />);

    // Web-parity disclosure: the summary is visible, detailed telemetry is not.
    expect(screen.getByText('View details')).toBeTruthy();
    expect(screen.queryByText('ETA (arrival)')).toBeNull();
    fireEvent.press(screen.getByLabelText('View details for Asha Rider'));

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
    expect(screen.getByText('climbed so far')).toBeTruthy();
    expect(screen.getByText('2345 ft')).toBeTruthy();
    expect(screen.getByText('+45m')).toBeTruthy();     // cutoff margin, signed
    expect(screen.getByText('stopped day 2')).toBeTruthy();
    expect(screen.getByText('stopped here')).toBeTruthy();
  });

  it('renders the active-day plan summary with event and Pacific times', () => {
    mockPositions([rider({})], CHART, { plan_snapshot: PLAN_SNAPSHOT });
    render(<RideLiveScreen />);
    expect(screen.getByText('DAY 2 PLAN')).toBeTruthy();
    expect(screen.getByText('Coulee Challenge')).toBeTruthy();
    expect(screen.getByText('182 mi')).toBeTruthy();
    expect(screen.getByText('West Salem')).toBeTruthy();
    expect(screen.getByText('8:10 AM CT')).toBeTruthy();
    expect(screen.getByText('6:10 AM PT')).toBeTruthy();
    expect(screen.getByText('Rider stops · day 2')).toBeTruthy();
    expect(screen.getByText('Asha Rider · Intermediate stop')).toBeTruthy();
    expect(screen.getByText('1:00 PM–1:04 PM')).toBeTruthy();
  });

  it('catalogs each rider stop separately beneath the shared day plan', () => {
    const bob = rider({ rider_id: 8, name: 'Bob Rider', color: '#dc2626', plan_color: '#dc2626' });
    bob.telemetry!.now!.stop_events = [{ distance_mi: 274, duration_min: 12, day_number: 2,
      start_label: '2:00 PM', end_label: '2:12 PM' }];
    mockPositions([rider({}), bob], CHART, { plan_snapshot: PLAN_SNAPSHOT });
    render(<RideLiveScreen />);
    expect(screen.getByText('Asha Rider · Intermediate stop')).toBeTruthy();
    expect(screen.getByText('Bob Rider · West Salem')).toBeTruthy();
    expect(screen.getByText('2:00 PM–2:12 PM')).toBeTruthy();
  });

  it('keeps Going riders visible when they are not sharing location', () => {
    mockPositions([rider({ rider_id: 8, name: 'Bharadwaj Rao', lat: null, lng: null,
      recorded_at: null, minutes_ago: null, source: null, telemetry: null,
      not_sharing: true })], null);
    render(<RideLiveScreen />);
    expect(screen.getByText(/Bharadwaj Rao/)).toBeTruthy();
    expect(screen.getByText('Not sharing location')).toBeTruthy();
  });

  it('shows an em-dash for required speed when the rider is behind', () => {
    mockPositions(
      [rider({ telemetry: { ...rider({}).telemetry!,
        next_control: { name: 'Control 1', type: 'control', distance_mi: 10, dist_to_go_mi: 5,
          arrival_time_min: 90, eta_iso: null, eta_label: '2:30 PM',
          required_mph: null, behind: true } } })],
      CHART);
    render(<RideLiveScreen />);
    fireEvent.press(screen.getByLabelText('View details for Asha Rider'));
    expect(screen.getByText('req speed')).toBeTruthy();
    // required_mph null → em-dash rather than a negative/absent value.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('omits the charts block when the backend sends no chart_data', () => {
    mockPositions([rider({})], null);
    render(<RideLiveScreen />);
    expect(screen.queryByText(/Route ahead/)).toBeNull();
    fireEvent.press(screen.getByLabelText('View details for Asha Rider'));
    // The rider details still render (default rider has a next-control block).
    expect(screen.getByText('ETA (arrival)')).toBeTruthy();
  });

  it('renders the plan selector when the ride has multiple plans (item 1)', () => {
    mockPositions([rider({})], CHART, { plans: PLANS, selected_plan_id: 'base' });
    render(<RideLiveScreen />);
    expect(screen.getByText('Base plan')).toBeTruthy();
    expect(screen.getByText('Fast · Alice')).toBeTruthy();       // owner shown
    expect(screen.getByText("Each rider's own plan")).toBeTruthy();
  });

  it('shows a base-plan label and no selector for a single-plan ride', () => {
    mockPositions([rider({})], CHART,
      { plans: [{ id: 'base', name: 'Base plan', owner: null, is_custom: false }], selected_plan_id: 'base' });
    render(<RideLiveScreen />);
    expect(screen.getByText('base plan')).toBeTruthy();          // the strong label text
  });

  it('renders the shared upcoming-controls list once, not per rider (item 2)', () => {
    mockPositions([rider({}), rider({ rider_id: 8, name: 'Bob Rider' })], CHART,
      { upcoming_controls: UPCOMING });
    render(<RideLiveScreen />);
    expect(screen.getByText('Upcoming controls')).toBeTruthy();
    expect(screen.getByText('Control 1')).toBeTruthy();          // ', CA' stripped
    expect(screen.getAllByText('9:30 AM')).toHaveLength(1);      // one ride-level entry
  });

  it('shows speed to finish alongside speed to next control (item 3)', () => {
    const p = rider({});
    p.telemetry!.finish = { name: 'Finish', type: 'finish', distance_mi: 60, dist_to_go_mi: 55,
      arrival_time_min: 200, eta_iso: null, eta_label: '5:00 PM', required_mph: 14, behind: false };
    mockPositions([p], CHART);
    render(<RideLiveScreen />);
    fireEvent.press(screen.getByLabelText('View details for Asha Rider'));
    expect(screen.getByText('To finish')).toBeTruthy();
    expect(screen.getByText('5:00 PM')).toBeTruthy();
    expect(screen.getByText('14 mph')).toBeTruthy();
    // Both the next-control and the finish req-speed cells are present.
    expect(screen.getAllByText('req speed')).toHaveLength(2);
  });

  it('shows an em-dash for speed to finish when the rider is behind (item 3)', () => {
    const p = rider({});
    p.telemetry!.finish = { name: 'Finish', type: 'finish', distance_mi: 60, dist_to_go_mi: 55,
      arrival_time_min: 200, eta_iso: null, eta_label: '5:00 PM', required_mph: null, behind: true };
    mockPositions([p], CHART);
    render(<RideLiveScreen />);
    fireEvent.press(screen.getByLabelText('View details for Asha Rider'));
    expect(screen.getByText('To finish')).toBeTruthy();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('keeps each rider collapsed independently and toggles explicit details', () => {
    mockPositions([rider({}), rider({ rider_id: 8, name: 'Bob Rider' })], CHART);
    render(<RideLiveScreen />);

    expect(screen.getAllByText('View details')).toHaveLength(2);
    expect(screen.queryByText('ETA (arrival)')).toBeNull();
    fireEvent.press(screen.getByLabelText('View details for Bob Rider'));
    expect(screen.getByLabelText('Hide details for Bob Rider')).toBeTruthy();
    expect(screen.getByLabelText('View details for Asha Rider')).toBeTruthy();
    expect(screen.getByText('ETA (arrival)')).toBeTruthy();
  });

  it('orders rider summaries, route profile, then the day plan', () => {
    mockPositions([rider({})], CHART, { plan_snapshot: PLAN_SNAPSHOT });
    const view = render(<RideLiveScreen />);
    const sectionOrder: string[] = [];
    const visit = (node: any) => {
      if (!node || typeof node === 'string') return;
      if (node.props?.testID) sectionOrder.push(node.props.testID);
      (node.children ?? []).forEach(visit);
    };
    visit(view.toJSON());
    expect(sectionOrder.indexOf('live-rider-7')).toBeLessThan(sectionOrder.indexOf('live-profile-section'));
    expect(sectionOrder.indexOf('live-profile-section')).toBeLessThan(sectionOrder.indexOf('live-plan-section'));
  });
});

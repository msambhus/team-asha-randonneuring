/**
 * mobile/__tests__/season.test.tsx — the "My Season" screen surfaces the
 * season-level SR *award* count ("SR×N") in the Super Randonneur card title, in
 * parity with the web app. useMySeason is mocked, so no network / provider tree.
 */
import React from 'react';
import { render, screen } from '@testing-library/react-native';
import SeasonScreen from '../app/season';
import * as useMySeasonHook from '../hooks/useMySeason';
import type { MySeasonResponse } from '../lib/types';

function season(sr: MySeasonResponse['sr']): MySeasonResponse {
  return {
    season: { name: '2025-2026' },
    stats: { distance_km: 1200, rides: 8, elevation_ft: 42000 },
    sr,
    rides_done: [],
    r12: { months: 2, active: false },
    career: { distance_km: 9000 },
    eddington: null,
  };
}

function mock(data: MySeasonResponse) {
  jest.spyOn(useMySeasonHook, 'useMySeason').mockReturnValue({
    data, isLoading: false, isError: false, refetch: jest.fn(), isRefetching: false,
  } as never);
}

describe('SeasonScreen Super Randonneur title', () => {
  afterEach(() => jest.restoreAllMocks());

  it('shows SR×2 when the rider completed two full series in the season', () => {
    mock(season({
      has_sr: true,
      distances_done: [200, 300, 400, 600],
      counts: { '200': 2, '300': 2, '400': 2, '600': 2 },
    }));
    render(<SeasonScreen />);
    expect(screen.getByText(/SR×2/)).toBeTruthy();
  });

  it('shows no SR×N for a single-series season (checkmark only)', () => {
    mock(season({
      has_sr: true,
      distances_done: [200, 300, 400, 600],
      counts: { '200': 1, '300': 1, '400': 1, '600': 1 },
    }));
    render(<SeasonScreen />);
    expect(screen.queryByText(/SR×/)).toBeNull();
    expect(screen.getByText(/Super Randonneur/)).toBeTruthy();
  });

  it('shows no SR×N when the season is not yet a Super Randonneur', () => {
    mock(season({
      has_sr: false,
      distances_done: [200, 300],
      counts: { '200': 2, '300': 1, '400': 0, '600': 0 },
    }));
    render(<SeasonScreen />);
    expect(screen.queryByText(/SR×/)).toBeNull();
  });
});

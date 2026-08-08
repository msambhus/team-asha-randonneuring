import { renderHook, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';
import * as Updates from 'expo-updates';
import { useOtaUpdates } from '../hooks/useOtaUpdates';

jest.mock('expo-updates', () => ({
  isEnabled: true,
  useUpdates: jest.fn(),
  checkForUpdateAsync: jest.fn(async () => ({ isAvailable: false })),
  fetchUpdateAsync: jest.fn(),
  reloadAsync: jest.fn(),
}));

describe('useOtaUpdates', () => {
  afterEach(() => jest.restoreAllMocks());

  it('makes clear that the app reloads and the phone does not restart', async () => {
    jest.mocked(Updates.useUpdates).mockReturnValue({ isUpdatePending: true } as never);
    const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);

    renderHook(() => useOtaUpdates());

    await waitFor(() => expect(alert).toHaveBeenCalled());
    const [title, message, buttons] = alert.mock.calls[0];
    expect(title).toBe('Update available');
    expect(message).toContain('Reload the app');
    expect(message).toContain('phone will not restart');
    expect(buttons?.map((button) => button.text)).toContain('Reload app');
  });
});

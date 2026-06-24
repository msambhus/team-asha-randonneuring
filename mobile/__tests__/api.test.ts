/**
 * mobile/__tests__/api.test.ts — apiFetch: Bearer attach, 401 handling, errors.
 */
import { apiFetch, storeToken, deleteToken, authHeaders } from '../lib/api';

describe('authHeaders', () => {
  it('attaches the Bearer token when present', () => {
    expect(authHeaders('abc')).toMatchObject({ Authorization: 'Bearer abc' });
  });
  it('omits Authorization when no token', () => {
    expect(authHeaders(null).Authorization).toBeUndefined();
  });
});

describe('apiFetch', () => {
  afterEach(async () => {
    await deleteToken();
    jest.restoreAllMocks();
  });

  it('sends the Bearer token and returns parsed JSON', async () => {
    await storeToken('tok-123');
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockResolvedValue({
      ok: true, status: 200, json: async () => ({ hello: 'world' }),
    } as never);

    const out = await apiFetch<{ hello: string }>('/x', () => {});
    expect(out).toEqual({ hello: 'world' });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123');
  });

  it('clears token + calls onLogout + throws on 401', async () => {
    await storeToken('tok-123');
    jest.spyOn(global, 'fetch' as never).mockResolvedValue({
      ok: false, status: 401, json: async () => ({}),
    } as never);
    const onLogout = jest.fn();

    await expect(apiFetch('/x', onLogout)).rejects.toThrow('Unauthorized');
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('throws API error on other non-2xx', async () => {
    jest.spyOn(global, 'fetch' as never).mockResolvedValue({
      ok: false, status: 500, json: async () => ({}),
    } as never);
    await expect(apiFetch('/x', () => {})).rejects.toThrow('API error 500');
  });
});

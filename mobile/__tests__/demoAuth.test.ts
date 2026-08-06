/**
 * mobile/__tests__/demoAuth.test.ts — the reviewer/demo login hits
 * POST /api/auth/demo and returns the same {token, rider_id, profile_complete}
 * shape as Google sign-in; a 404 (demo disabled on the server) surfaces a
 * friendly error. Google native module is mocked so lib/auth imports cleanly.
 */
import { demoDeleteSignIn, demoSignIn } from '../lib/auth';

jest.mock('@react-native-google-signin/google-signin', () => ({
  GoogleSignin: {
    configure: jest.fn(), hasPlayServices: jest.fn(), signIn: jest.fn(),
    getTokens: jest.fn(), signOut: jest.fn(),
  },
}));

describe('demoSignIn', () => {
  afterEach(() => jest.restoreAllMocks());

  it('POSTs to /api/auth/demo and returns the token payload', async () => {
    const payload = { token: 't-1', rider_id: 7, profile_complete: true };
    const spy = jest
      .spyOn(global, 'fetch' as never)
      .mockResolvedValue({ ok: true, json: async () => payload } as never);

    const res = await demoSignIn();

    expect(res).toEqual(payload);
    const [url, opts] = (spy as unknown as jest.Mock).mock.calls[0];
    expect(String(url)).toContain('/api/auth/demo');
    expect((opts as { method: string }).method).toBe('POST');
  });

  it('throws a friendly error when demo mode is disabled (404)', async () => {
    jest.spyOn(global, 'fetch' as never).mockResolvedValue({ ok: false, status: 404 } as never);
    await expect(demoSignIn()).rejects.toThrow('Demo login is not available');
  });

  it('throws on other server errors', async () => {
    jest.spyOn(global, 'fetch' as never).mockResolvedValue({ ok: false, status: 500 } as never);
    await expect(demoSignIn()).rejects.toThrow('Demo sign-in failed (500)');
  });

  it('uses a separate resettable identity for the deletion recording', async () => {
    const payload = { token: 'delete-token', rider_id: 7, profile_complete: true };
    const spy = jest
      .spyOn(global, 'fetch' as never)
      .mockResolvedValue({ ok: true, json: async () => payload } as never);

    await expect(demoDeleteSignIn()).resolves.toEqual(payload);
    expect(String((spy as unknown as jest.Mock).mock.calls[0][0]))
      .toContain('/api/auth/demo-delete');
  });
});

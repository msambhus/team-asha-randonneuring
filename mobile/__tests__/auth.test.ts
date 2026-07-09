/**
 * Unit tests for the fetch-based auth helpers (Apple token exchange + account
 * deletion). Native modules are mocked so this runs in the jest-expo node env.
 */
import { exchangeAppleToken, deleteAccount, passwordSignup, passwordLogin } from '../lib/auth';

// Native modules pulled in transitively by lib/auth.ts.
jest.mock('@react-native-google-signin/google-signin', () => ({ GoogleSignin: {} }));
jest.mock('expo-apple-authentication', () => ({
  isAvailableAsync: jest.fn(),
  signInAsync: jest.fn(),
  AppleAuthenticationScope: { FULL_NAME: 0, EMAIL: 1 },
}));
jest.mock('../lib/api', () => ({
  authHeaders: (t: string | null) => ({ 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) }),
  getToken: jest.fn(async () => 'stored-token'),
}));

const okJson = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
const errStatus = (status: number) => Promise.resolve({ ok: false, status, json: () => Promise.resolve({}) });

afterEach(() => jest.restoreAllMocks());

describe('exchangeAppleToken', () => {
  it('POSTs identity_token + email and returns the session', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ token: 'app-tok', rider_id: 7, profile_complete: true }) as never,
    );
    const res = await exchangeAppleToken('apple-id-tok', 'a@b.com');
    expect(res).toEqual({ token: 'app-tok', rider_id: 7, profile_complete: true });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/auth/apple');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ identity_token: 'apple-id-tok', email: 'a@b.com' });
  });

  it('maps a 401 to a friendly rejection', async () => {
    jest.spyOn(global, 'fetch' as never).mockReturnValue(errStatus(401) as never);
    await expect(exchangeAppleToken('t', null)).rejects.toThrow('Apple sign-in was rejected');
  });
});

describe('deleteAccount', () => {
  it('sends DELETE with the bearer token', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      Promise.resolve({ ok: true, status: 200 }) as never,
    );
    await deleteAccount();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/auth/account');
    expect(init.method).toBe('DELETE');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer stored-token');
  });

  it('throws on a non-2xx response', async () => {
    jest.spyOn(global, 'fetch' as never).mockReturnValue(errStatus(500) as never);
    await expect(deleteAccount()).rejects.toThrow('Account deletion failed (500)');
  });
});


const errBody = (status: number, body: unknown) =>
  Promise.resolve({ ok: false, status, json: () => Promise.resolve(body) });

describe('passwordSignup', () => {
  it('POSTs email + password to /signup and returns the session', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ token: 'app-tok', rider_id: null, profile_complete: false }) as never,
    );
    const res = await passwordSignup('a@b.com', 'longenough1');
    expect(res).toEqual({ token: 'app-tok', rider_id: null, profile_complete: false });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/auth/signup');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ email: 'a@b.com', password: 'longenough1' });
  });

  it('surfaces the backend error message (e.g. email already exists)', async () => {
    jest.spyOn(global, 'fetch' as never).mockReturnValue(
      errBody(409, { error: 'An account with this email already exists. Try signing in.' }) as never,
    );
    await expect(passwordSignup('a@b.com', 'longenough1')).rejects.toThrow('already exists');
  });
});

describe('passwordLogin', () => {
  it('POSTs email + password to /login and returns the session', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ token: 'app-tok', rider_id: 7, profile_complete: true }) as never,
    );
    const res = await passwordLogin('a@b.com', 'longenough1');
    expect(res.token).toBe('app-tok');
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/auth/login');
  });

  it('surfaces the backend 401 message', async () => {
    jest.spyOn(global, 'fetch' as never).mockReturnValue(
      errBody(401, { error: 'Incorrect email or password' }) as never,
    );
    await expect(passwordLogin('a@b.com', 'wrong')).rejects.toThrow('Incorrect email or password');
  });
});

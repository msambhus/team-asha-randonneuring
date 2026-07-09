/**
 * Unit tests for the fetch-based auth helpers: email + password, passwordless
 * email OTP (request + verify), and account deletion. (Google + Sign in with
 * Apple were removed for App Store Guideline 4.8.)
 */
import {
  deleteAccount, passwordSignup, passwordLogin,
  requestEmailOtp, verifyEmailOtp,
} from '../lib/auth';

jest.mock('../lib/api', () => ({
  authHeaders: (t: string | null) => ({ 'Content-Type': 'application/json', ...(t ? { Authorization: `Bearer ${t}` } : {}) }),
  getToken: jest.fn(async () => 'stored-token'),
}));

const okJson = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
const errStatus = (status: number) => Promise.resolve({ ok: false, status, json: () => Promise.resolve({}) });
const errBody = (status: number, body: unknown) =>
  Promise.resolve({ ok: false, status, json: () => Promise.resolve(body) });

afterEach(() => jest.restoreAllMocks());

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

describe('requestEmailOtp', () => {
  it('POSTs the email to /otp/request', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ message: 'sent' }) as never,
    );
    await requestEmailOtp('rider@example.com');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/auth/otp/request');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ email: 'rider@example.com' });
  });

  it('surfaces a rate-limit message', async () => {
    jest.spyOn(global, 'fetch' as never).mockReturnValue(
      errBody(429, { error: 'Too many code requests. Try again later.' }) as never,
    );
    await expect(requestEmailOtp('r@example.com')).rejects.toThrow('Too many code requests');
  });
});

describe('verifyEmailOtp', () => {
  it('POSTs email + code (+ optional phone) and returns the session', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ token: 'app-tok', rider_id: 7, profile_complete: true }) as never,
    );
    const res = await verifyEmailOtp({ email: 'rider@example.com', code: '123456', phone: '+15551234567' });
    expect(res).toEqual({ token: 'app-tok', rider_id: 7, profile_complete: true });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/auth/otp/verify');
    expect(JSON.parse(init.body as string)).toEqual({
      email: 'rider@example.com', code: '123456', phone: '+15551234567',
    });
  });

  it('omits phone when not provided', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ token: 't', rider_id: null, profile_complete: false }) as never,
    );
    await verifyEmailOtp({ email: 'r@example.com', code: '000111' });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ email: 'r@example.com', code: '000111' });
  });

  it('sends a magic-link token as link_token', async () => {
    const fetchMock = jest.spyOn(global, 'fetch' as never).mockReturnValue(
      okJson({ token: 't', rider_id: 7, profile_complete: true }) as never,
    );
    await verifyEmailOtp({ linkToken: 'magic-abc' });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ link_token: 'magic-abc' });
  });

  it('surfaces the backend error on a bad code', async () => {
    jest.spyOn(global, 'fetch' as never).mockReturnValue(
      errBody(401, { error: 'Incorrect or expired code' }) as never,
    );
    await expect(verifyEmailOtp({ email: 'r@example.com', code: '999999' }))
      .rejects.toThrow('Incorrect or expired code');
  });
});

/**
 * mobile/contexts/SessionContext.tsx — token lifecycle + first-party sign-in.
 *
 * On mount reads the stored token once (block route guards on isLoading).
 * Sign-in is email + password or passwordless email OTP (code or magic link);
 * Google + Sign in with Apple were removed (App Store Guideline 4.8).
 * signOut() clears the token + query cache.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useState,
} from 'react';
import * as SecureStore from 'expo-secure-store';
import { useQueryClient } from '@tanstack/react-query';
import { getToken, storeToken, deleteToken } from '../lib/api';
import {
  demoSignIn, demoDeleteSignIn, deleteAccount as deleteAccountApi,
  passwordLogin, passwordSignup,
  requestEmailOtp as requestEmailOtpApi,
  verifyEmailOtp as verifyEmailOtpApi,
  setupProfile as setupProfileApi,
  type OtpVerifyParams,
} from '../lib/auth';
import type { GoogleAuthResponse } from '../lib/types';

const RIDER_KEY = 'ta_rider_id';
const PROFILE_KEY = 'ta_profile_complete';
// The account email is captured at sign-in (when we know it) so the account
// screen can require the user to retype it before deleting. Not known for the
// demo login or a magic-link-only session — those fall back to a DELETE keyword.
const EMAIL_KEY = 'ta_account_email';

interface SessionValue {
  token: string | null;
  riderId: number | null;
  profileComplete: boolean;
  /** The signed-in account's email, if known (used to confirm deletion). */
  accountEmail: string | null;
  isLoading: boolean;
  /** Reviewer/demo login (no third party). Returns null on success, else an error string. */
  signInDemo: () => Promise<string | null>;
  /** Resettable demo identity for recording the permanent deletion flow. */
  signInDemoDelete: () => Promise<string | null>;
  /** Email + password sign-in / sign-up. Returns null on success, else an error string. */
  signInWithPassword: (email: string, password: string) => Promise<string | null>;
  signUpWithPassword: (email: string, password: string) => Promise<string | null>;
  /** Email OTP: request a code, then verify a code or magic-link token. Each
   *  returns null on success, else an error string to display. */
  requestEmailOtp: (email: string) => Promise<string | null>;
  verifyEmailOtp: (params: OtpVerifyParams) => Promise<string | null>;
  /** Link the account to a RUSA rider (onboarding). Returns null on success,
   *  else an error string. On success the session updates to profile-complete. */
  setupProfile: (rusaId: string) => Promise<string | null>;
  signOut: () => Promise<void>;
  /** Permanently delete the account, then sign out. Returns null on success,
   *  else an error string. */
  deleteAccount: () => Promise<string | null>;
}

const SessionContext = createContext<SessionValue>({
  token: null,
  riderId: null,
  profileComplete: false,
  accountEmail: null,
  isLoading: true,
  signInDemo: async () => 'not ready',
  signInDemoDelete: async () => 'not ready',
  signInWithPassword: async () => 'not ready',
  signUpWithPassword: async () => 'not ready',
  requestEmailOtp: async () => 'not ready',
  verifyEmailOtp: async () => 'not ready',
  setupProfile: async () => 'not ready',
  signOut: async () => undefined,
  deleteAccount: async () => 'not ready',
});

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [riderId, setRiderId] = useState<number | null>(null);
  const [profileComplete, setProfileComplete] = useState(false);
  const [accountEmail, setAccountEmail] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const queryClient = useQueryClient();

  useEffect(() => {
    (async () => {
      try {
        setToken(await getToken());
        const r = await SecureStore.getItemAsync(RIDER_KEY);
        if (r) setRiderId(parseInt(r, 10));
        setProfileComplete((await SecureStore.getItemAsync(PROFILE_KEY)) === '1');
        setAccountEmail((await SecureStore.getItemAsync(EMAIL_KEY)) || null);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  // Persist + apply an auth response (shared by every sign-in path). `email` is
  // passed by the paths that know it (password + OTP-code) so the account screen
  // can require it to confirm deletion; omitted for demo / magic-link.
  const applySession = useCallback(async (res: GoogleAuthResponse, email?: string) => {
    await storeToken(res.token);
    await SecureStore.setItemAsync(RIDER_KEY, res.rider_id == null ? '' : String(res.rider_id));
    await SecureStore.setItemAsync(PROFILE_KEY, res.profile_complete ? '1' : '0');
    const cleanEmail = (email || '').trim();
    if (cleanEmail) {
      await SecureStore.setItemAsync(EMAIL_KEY, cleanEmail);
      setAccountEmail(cleanEmail);
    }
    setToken(res.token);
    setRiderId(res.rider_id);
    setProfileComplete(res.profile_complete);
    queryClient.clear();
  }, [queryClient]);

  const signInDemo = useCallback(async (): Promise<string | null> => {
    try {
      await applySession(await demoSignIn());
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Demo sign-in failed';
    }
  }, [applySession]);

  const signInDemoDelete = useCallback(async (): Promise<string | null> => {
    try {
      await applySession(await demoDeleteSignIn());
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Deletion demo sign-in failed';
    }
  }, [applySession]);

  const signInWithPassword = useCallback(
    async (email: string, password: string): Promise<string | null> => {
      try {
        await applySession(await passwordLogin(email, password), email);
        return null;
      } catch (e) {
        return e instanceof Error ? e.message : 'Sign-in failed';
      }
    },
    [applySession],
  );

  const signUpWithPassword = useCallback(
    async (email: string, password: string): Promise<string | null> => {
      try {
        await applySession(await passwordSignup(email, password), email);
        return null;
      } catch (e) {
        return e instanceof Error ? e.message : 'Sign-up failed';
      }
    },
    [applySession],
  );

  // Requesting a code does NOT establish a session — it just emails the code.
  const requestEmailOtp = useCallback(async (email: string): Promise<string | null> => {
    try {
      await requestEmailOtpApi(email);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Could not send a code';
    }
  }, []);

  const verifyEmailOtp = useCallback(
    async (params: OtpVerifyParams): Promise<string | null> => {
      try {
        // The code path carries the email; the magic-link path does not.
        const email = 'email' in params ? params.email : undefined;
        await applySession(await verifyEmailOtpApi(params), email);
        return null;
      } catch (e) {
        return e instanceof Error ? e.message : 'Sign-in failed';
      }
    },
    [applySession],
  );

  const setupProfile = useCallback(async (rusaId: string): Promise<string | null> => {
    try {
      // Reuses applySession: the response carries a new token with the rider_id,
      // and setting profileComplete=true flips the app out of Onboarding.
      await applySession(await setupProfileApi(rusaId));
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Profile setup failed';
    }
  }, [applySession]);

  const signOut = useCallback(async () => {
    await deleteToken();
    await SecureStore.deleteItemAsync(RIDER_KEY).catch(() => undefined);
    await SecureStore.deleteItemAsync(PROFILE_KEY).catch(() => undefined);
    await SecureStore.deleteItemAsync(EMAIL_KEY).catch(() => undefined);
    setToken(null);
    setRiderId(null);
    setProfileComplete(false);
    setAccountEmail(null);
    queryClient.clear();
  }, [queryClient]);

  const deleteAccount = useCallback(async (): Promise<string | null> => {
    try {
      await deleteAccountApi();
    } catch (e) {
      return e instanceof Error ? e.message : 'Account deletion failed';
    }
    // Deletion succeeded on the server — clear all local state.
    await signOut();
    return null;
  }, [signOut]);

  return (
    <SessionContext.Provider
      value={{
        token, riderId, profileComplete, accountEmail, isLoading,
        signInDemo, signInDemoDelete, signInWithPassword, signUpWithPassword,
        requestEmailOtp, verifyEmailOtp, setupProfile, signOut, deleteAccount,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext);

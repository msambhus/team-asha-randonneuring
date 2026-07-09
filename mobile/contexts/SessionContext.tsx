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
  demoSignIn, deleteAccount as deleteAccountApi,
  passwordLogin, passwordSignup,
  requestEmailOtp as requestEmailOtpApi,
  verifyEmailOtp as verifyEmailOtpApi,
  type OtpVerifyParams,
} from '../lib/auth';
import type { GoogleAuthResponse } from '../lib/types';

const RIDER_KEY = 'ta_rider_id';
const PROFILE_KEY = 'ta_profile_complete';

interface SessionValue {
  token: string | null;
  riderId: number | null;
  profileComplete: boolean;
  isLoading: boolean;
  /** Reviewer/demo login (no third party). Returns null on success, else an error string. */
  signInDemo: () => Promise<string | null>;
  /** Email + password sign-in / sign-up. Returns null on success, else an error string. */
  signInWithPassword: (email: string, password: string) => Promise<string | null>;
  signUpWithPassword: (email: string, password: string) => Promise<string | null>;
  /** Email OTP: request a code, then verify a code or magic-link token. Each
   *  returns null on success, else an error string to display. */
  requestEmailOtp: (email: string) => Promise<string | null>;
  verifyEmailOtp: (params: OtpVerifyParams) => Promise<string | null>;
  signOut: () => Promise<void>;
  /** Permanently delete the account, then sign out. Returns null on success,
   *  else an error string. */
  deleteAccount: () => Promise<string | null>;
}

const SessionContext = createContext<SessionValue>({
  token: null,
  riderId: null,
  profileComplete: false,
  isLoading: true,
  signInDemo: async () => 'not ready',
  signInWithPassword: async () => 'not ready',
  signUpWithPassword: async () => 'not ready',
  requestEmailOtp: async () => 'not ready',
  verifyEmailOtp: async () => 'not ready',
  signOut: async () => undefined,
  deleteAccount: async () => 'not ready',
});

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [riderId, setRiderId] = useState<number | null>(null);
  const [profileComplete, setProfileComplete] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const queryClient = useQueryClient();

  useEffect(() => {
    (async () => {
      try {
        setToken(await getToken());
        const r = await SecureStore.getItemAsync(RIDER_KEY);
        if (r) setRiderId(parseInt(r, 10));
        setProfileComplete((await SecureStore.getItemAsync(PROFILE_KEY)) === '1');
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  // Persist + apply an auth response (shared by every sign-in path).
  const applySession = useCallback(async (res: GoogleAuthResponse) => {
    await storeToken(res.token);
    await SecureStore.setItemAsync(RIDER_KEY, res.rider_id == null ? '' : String(res.rider_id));
    await SecureStore.setItemAsync(PROFILE_KEY, res.profile_complete ? '1' : '0');
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

  const signInWithPassword = useCallback(
    async (email: string, password: string): Promise<string | null> => {
      try {
        await applySession(await passwordLogin(email, password));
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
        await applySession(await passwordSignup(email, password));
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
        await applySession(await verifyEmailOtpApi(params));
        return null;
      } catch (e) {
        return e instanceof Error ? e.message : 'Sign-in failed';
      }
    },
    [applySession],
  );

  const signOut = useCallback(async () => {
    await deleteToken();
    await SecureStore.deleteItemAsync(RIDER_KEY).catch(() => undefined);
    await SecureStore.deleteItemAsync(PROFILE_KEY).catch(() => undefined);
    setToken(null);
    setRiderId(null);
    setProfileComplete(false);
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
        token, riderId, profileComplete, isLoading,
        signInDemo, signInWithPassword, signUpWithPassword,
        requestEmailOtp, verifyEmailOtp, signOut, deleteAccount,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext);

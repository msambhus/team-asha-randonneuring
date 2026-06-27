/**
 * mobile/contexts/SessionContext.tsx — token lifecycle + Google sign-in.
 *
 * On mount reads the stored token once (block route guards on isLoading).
 * signInWithGoogle() runs native Google sign-in → exchanges for our app token.
 * signOut() clears the token + Google session + query cache.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useState,
} from 'react';
import * as SecureStore from 'expo-secure-store';
import { useQueryClient } from '@tanstack/react-query';
import { getToken, storeToken, deleteToken } from '../lib/api';
import { getGoogleIdToken, exchangeGoogleToken, demoSignIn, googleSignOut } from '../lib/auth';
import type { GoogleAuthResponse } from '../lib/types';

const RIDER_KEY = 'ta_rider_id';
const PROFILE_KEY = 'ta_profile_complete';

interface SessionValue {
  token: string | null;
  riderId: number | null;
  profileComplete: boolean;
  isLoading: boolean;
  /** Returns null on success, else an error string to display. */
  signInWithGoogle: () => Promise<string | null>;
  /** Reviewer/demo login (no Google). Returns null on success, else an error string. */
  signInDemo: () => Promise<string | null>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({
  token: null,
  riderId: null,
  profileComplete: false,
  isLoading: true,
  signInWithGoogle: async () => 'not ready',
  signInDemo: async () => 'not ready',
  signOut: async () => undefined,
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

  // Persist + apply an auth response (shared by Google and demo login).
  const applySession = useCallback(async (res: GoogleAuthResponse) => {
    await storeToken(res.token);
    await SecureStore.setItemAsync(RIDER_KEY, res.rider_id == null ? '' : String(res.rider_id));
    await SecureStore.setItemAsync(PROFILE_KEY, res.profile_complete ? '1' : '0');
    setToken(res.token);
    setRiderId(res.rider_id);
    setProfileComplete(res.profile_complete);
    queryClient.clear();
  }, [queryClient]);

  const signInWithGoogle = useCallback(async (): Promise<string | null> => {
    try {
      const idToken = await getGoogleIdToken();
      if (idToken === null) return null;   // user cancelled — not an error
      await applySession(await exchangeGoogleToken(idToken));
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Sign-in failed';
    }
  }, [applySession]);

  const signInDemo = useCallback(async (): Promise<string | null> => {
    try {
      await applySession(await demoSignIn());
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : 'Demo sign-in failed';
    }
  }, [applySession]);

  const signOut = useCallback(async () => {
    await deleteToken();
    await SecureStore.deleteItemAsync(RIDER_KEY).catch(() => undefined);
    await SecureStore.deleteItemAsync(PROFILE_KEY).catch(() => undefined);
    await googleSignOut();
    setToken(null);
    setRiderId(null);
    setProfileComplete(false);
    queryClient.clear();
  }, [queryClient]);

  return (
    <SessionContext.Provider
      value={{ token, riderId, profileComplete, isLoading, signInWithGoogle, signInDemo, signOut }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext);

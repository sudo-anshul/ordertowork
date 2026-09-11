import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api, ApiError, errorMessage, post, SESSION_EXPIRED_EVENT, setCsrfToken } from './api';
import type { AuthConfig, Session } from './types';

interface AuthContextValue {
  session: Session | null;
  config: AuthConfig | null;
  loading: boolean;
  error: string | null;
  sessionNotice: string | null;
  refresh: () => Promise<void>;
  acceptSession: (session: Session) => void;
  logout: () => Promise<void>;
}
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);

  const acceptSession = useCallback((value: Session) => {
    setSession(value);
    setCsrfToken(value.csrf_token);
    setError(null);
    setSessionNotice(null);
  }, []);
  const refresh = useCallback(async () => {
    setError(null);
    const responses = await Promise.allSettled([
      api<AuthConfig>('/auth/config'),
      api<Session>('/auth/me'),
    ]);
    const [configuration, current] = responses;
    if (configuration.status === 'fulfilled') setConfig(configuration.value);
    else setError(errorMessage(configuration.reason));
    if (current.status === 'fulfilled') acceptSession(current.value);
    else if (current.reason instanceof ApiError && current.reason.status === 401) {
      setSession(null);
      setCsrfToken(null);
    } else setError(errorMessage(current.reason));
    setLoading(false);
  }, [acceptSession]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!session) return;
    const expire = () => {
      setSession(null);
      setCsrfToken(null);
      setSessionNotice(
        session.auth_method === 'demo'
          ? 'Your demo session has ended. Start a fresh demo to explore again.'
          : session.auth_method === 'reviewer'
            ? 'Your reviewer session has ended. Open your reviewer link to continue.'
            : 'Your session has ended. Sign in again to continue.',
      );
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, expire);
    const access = session.reviewer ?? session.demo;
    const expiresAt = access ? Date.parse(access.expires_at) : NaN;
    let timeout: number | undefined;
    const checkExpiry = () => {
      if (!Number.isFinite(expiresAt)) return;
      const remaining = expiresAt - Date.now();
      if (remaining <= 0) expire();
      // Browser timers overflow after ~24.8 days. Reviewer sessions can last longer.
      else timeout = window.setTimeout(checkExpiry, Math.min(remaining, 2147483647));
    };
    checkExpiry();
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, expire);
      window.clearTimeout(timeout);
    };
  }, [session]);

  const logout = async () => {
    let result: { ok: boolean; logout_url: string | null };
    try {
      result = await post<{ ok: boolean; logout_url: string | null }>('/auth/logout');
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
      result = { ok: true, logout_url: null };
    }
    setSession(null);
    setCsrfToken(null);
    setSessionNotice(null);
    if (result.logout_url) window.location.assign(result.logout_url);
  };
  return (
    <AuthContext.Provider
      value={{ session, config, loading, error, sessionNotice, refresh, acceptSession, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('AuthProvider is required');
  return value;
}

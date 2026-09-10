import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { api, ApiError, errorMessage, post, setCsrfToken } from './api';
import type { AuthConfig, Session } from './types';

interface AuthContextValue {
  session: Session | null;
  config: AuthConfig | null;
  loading: boolean;
  error: string | null;
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

  const acceptSession = useCallback((value: Session) => {
    setSession(value);
    setCsrfToken(value.csrf_token);
    setError(null);
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

  const logout = async () => {
    const result = await post<{ ok: boolean; logout_url: string | null }>('/auth/logout');
    setSession(null);
    setCsrfToken(null);
    if (result.logout_url) window.location.assign(result.logout_url);
  };
  return (
    <AuthContext.Provider
      value={{ session, config, loading, error, refresh, acceptSession, logout }}
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

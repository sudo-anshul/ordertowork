import { ArrowRight, ShieldCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Brand, Button, ErrorNotice, Loading } from '../components/ui';
import { ApiError, errorMessage, post } from '../lib/api';
import { useAuth } from '../lib/auth';
import { sampleDestination } from '../lib/demo';
import type { Session } from '../lib/types';

export function ReviewerPage() {
  const { session, loading, acceptSession } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  // Keep the link capability only in memory; never in query strings or browser storage.
  const token = useRef(location.hash.slice(1));
  const processedFragment = useRef<string | null>(null);
  const operation = useRef<Promise<Session> | null>(null);
  const replaceBusiness = useRef(false);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [switchNeeded, setSwitchNeeded] = useState(false);
  const [pending, setPending] = useState(true);

  useEffect(() => {
    document.title = 'Reviewer access · OrderToWork';
    if (!location.hash) {
      processedFragment.current = null;
      return;
    }
    // Opening a link on an already-loaded /review page is a fragment navigation,
    // not a component remount. Capture that new capability before clearing the URL.
    if (processedFragment.current !== location.hash) {
      processedFragment.current = location.hash;
      token.current = location.hash.slice(1);
      operation.current = null;
      replaceBusiness.current = false;
      setError(null);
      setSwitchNeeded(false);
      setPending(true);
      setAttempt((value) => value + 1);
    }
    navigate('/review', { replace: true });
  }, [location.hash, navigate]);

  useEffect(() => {
    if (loading) return;
    if (!token.current && session?.auth_method !== 'reviewer') {
      setPending(false);
      setError(
        'Open the complete reviewer link supplied with the project to enter your workspace.',
      );
      return;
    }
    if (!operation.current) {
      operation.current =
        session?.auth_method === 'reviewer'
          ? Promise.resolve(session)
          : post<Session>('/auth/reviewer', {
              token: token.current,
              replace_business_session: replaceBusiness.current,
            });
    }
    // Reuse the same request during React's effect replay; attach a live listener each time.
    let active = true;
    void operation.current
      .then(async (result) => {
        const destination = await sampleDestination(result);
        if (!active) return;
        token.current = '';
        acceptSession(result);
        navigate(destination, { replace: true });
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setPending(false);
        setSwitchNeeded(cause instanceof ApiError && cause.code === 'business_session_active');
        setError(errorMessage(cause));
      });
    return () => {
      active = false;
    };
  }, [loading, attempt, session, acceptSession, navigate]);

  const retry = (switchSession = false) => {
    replaceBusiness.current = switchSession;
    operation.current = null;
    setError(null);
    setSwitchNeeded(false);
    setPending(true);
    setAttempt((value) => value + 1);
  };

  return (
    <div className="standalone">
      <Brand />
      <section className="panel panel-padded section-gap">
        <p className="eyebrow">
          <ShieldCheck size={16} /> Project reviewer
        </p>
        <h1>Your workbench is ready to explore.</h1>
        <p className="muted">
          Your own sample businesses, with live analysis and the full order workflow. No account
          setup required.
        </p>
        {pending ? (
          <Loading label="Preparing your private workspaces…" />
        ) : (
          <>
            <ErrorNotice message={error} />
            {switchNeeded ? (
              <Button variant="primary" onClick={() => retry(true)}>
                Switch to reviewer workspace <ArrowRight size={16} />
              </Button>
            ) : token.current ? (
              <Button onClick={() => retry()}>Try opening again</Button>
            ) : null}
            <p className="section-gap">
              <Link to="/" className="subtle-link">
                Back to OrderToWork
              </Link>
            </p>
          </>
        )}
      </section>
    </div>
  );
}

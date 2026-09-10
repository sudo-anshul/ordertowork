import {
  ArrowRight,
  Check,
  ClipboardCheck,
  KeyRound,
  LogOut,
  MessageSquareText,
  Play,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { api, ApiError, post } from '../lib/api';
import { useAction } from '../lib/hooks';
import type { OrderSummary, Session, Workspace } from '../lib/types';
import { Badge, Brand, Button, ErrorNotice, Field, Loading, Notice } from '../components/ui';
import { RuntimeBar } from '../components/layout';

export function LoginPage() {
  const { config, session, loading, error, sessionNotice, refresh, acceptSession } = useAuth();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [destination, setDestination] = useState('/');
  const action = useAction();
  const demoAction = useAction();
  const location = useLocation();
  useEffect(() => {
    document.title = 'Welcome · OrderToWork';
  }, []);
  if (loading) return <Loading label="Getting sign-in ready…" />;
  if (session) return <Navigate to={destination} replace />;
  const login = (e: FormEvent) => {
    e.preventDefault();
    void action.run(
      () => post<Session>('/auth/development-login', { email, name }),
      (result) => {
        const requestedDestination = (location.state as { from?: string } | null)?.from;
        setDestination(requestedDestination?.startsWith('/w/') ? requestedDestination : '/');
        acceptSession(result);
      },
    );
  };
  const startDemo = () => {
    void demoAction.run(
      async () => {
        let result: Session;
        try {
          result = await api<Session>('/auth/demo', { method: 'POST' });
        } catch (error) {
          if (error instanceof ApiError && error.status === 429) {
            if (error.code === 'demo_capacity_reached') throw error;
            throw new Error('The demo has reached its visitor limit. Please try again later.');
          }
          if (error instanceof ApiError && error.status === 503)
            throw new Error('The demo is temporarily unavailable. Please try again shortly.');
          throw error;
        }
        const workspace = result.workspaces[0];
        let destination = workspace ? `/w/${workspace.id}/decisions` : '/';
        if (workspace) {
          try {
            const { orders } = await api<{ orders: OrderSummary[] }>(
              `/workspaces/${workspace.id}/orders`,
            );
            const order = orders.find((item) => item.is_demo);
            if (order) destination = `/w/${workspace.id}/orders/${order.id}`;
          } catch {
            // A usable session still opens if the first order needs another fetch.
          }
        }
        return { session: result, destination };
      },
      (result) => {
        setDestination(result.destination);
        acceptSession(result.session);
      },
    );
  };
  return (
    <div className="auth-page">
      <div className="auth-story">
        <Brand />
        <div className="auth-story-copy">
          <p className="eyebrow">For people who make things</p>
          <h1>
            A promise made.
            <br />
            <span>Ready for work.</span>
          </h1>
          <p>
            Turn customer requests into clear agreements, reliable plans, and work your team can
            start with confidence.
          </p>
        </div>
        <div className="story-steps">
          <div>
            <MessageSquareText size={20} />
            <span>Understand the request</span>
          </div>
          <div>
            <ShieldCheck size={20} />
            <span>Check what’s possible</span>
          </div>
          <div>
            <ClipboardCheck size={20} />
            <span>Hand over agreed work</span>
            <Check size={17} className="story-check" />
          </div>
        </div>
        <p className="auth-story-footer">Your attention, where it matters.</p>
      </div>
      <div className="auth-form-side">
        <div className="auth-mobile-brand">
          <Brand />
        </div>
        <div className="auth-form">
          <div className="auth-icon">
            {config?.demo_enabled ? (
              <Play size={24} strokeWidth={1.5} />
            ) : (
              <KeyRound size={24} strokeWidth={1.5} />
            )}
          </div>
          <p className="eyebrow">Your workbench</p>
          <h2>
            {config?.demo_enabled
              ? 'Good work starts with a clear promise.'
              : 'Welcome to OrderToWork.'}
          </h2>
          <p className="muted">
            {config?.demo_enabled
              ? 'Explore the workbench, or sign in to keep your business moving.'
              : 'Sign in to keep the details, decisions, and next steps in one place.'}
          </p>
          <ErrorNotice message={error} retry={() => void refresh()} />
          {sessionNotice && <Notice title="Welcome back.">{sessionNotice}</Notice>}
          {config?.demo_enabled && (
            <>
              <section className="demo-entry" aria-labelledby="demo-entry-title">
                <div className="demo-entry-kicker">
                  <span className="demo-entry-dot" aria-hidden="true" />A workspace ready to explore
                </div>
                <h3 id="demo-entry-title">The customer changed their mind. Now what?</h3>
                <p>
                  Compare workable options, get the customer’s approval, and hand the right order to
                  production.
                </p>
                <div className="demo-entry-businesses" aria-label="Included examples">
                  <span>Merchandise studio</span>
                  <span>Neighborhood bakery</span>
                </div>
                <Button
                  variant="primary"
                  className="full-width demo-entry-button"
                  busy={demoAction.pending}
                  disabled={action.pending}
                  onClick={startDemo}
                >
                  {demoAction.pending ? 'Preparing your workbench…' : 'Try the live demo'}
                  {!demoAction.pending && <ArrowRight size={18} />}
                </Button>
                <p className="demo-entry-details">No sign-up · Your own sample data · 60 minutes</p>
                <ErrorNotice message={demoAction.error} title="The demo couldn’t open yet." />
              </section>
              <div className="auth-divider">
                <span>Have a business account?</span>
              </div>
            </>
          )}
          {config?.development_login_enabled ? (
            <>
              <Notice title="Development sign-in" tone="warning">
                This local environment creates a development identity. Use Cognito sign-in for
                deployed accounts.
              </Notice>
              <form onSubmit={login} className="form-stack">
                <Field label="Your name" htmlFor="login-name">
                  <input
                    id="login-name"
                    autoComplete="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    maxLength={120}
                    placeholder="Full name"
                  />
                </Field>
                <Field label="Email address" htmlFor="login-email">
                  <input
                    id="login-email"
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    placeholder="you@yourbusiness.com"
                  />
                </Field>
                <ErrorNotice message={action.error} />
                <Button
                  variant="primary"
                  busy={action.pending}
                  disabled={demoAction.pending}
                  type="submit"
                  className="full-width"
                >
                  Continue to your workspace <ArrowRight size={17} />
                </Button>
              </form>
            </>
          ) : config?.configured && config.login_url ? (
            <div className="section-gap">
              <a
                className={`button button-${config.demo_enabled ? 'secondary' : 'primary'} full-width`}
                href={`/api${config.login_url.replace(/^\/api/, '')}`}
                aria-disabled={demoAction.pending || undefined}
                onClick={(event) => {
                  if (demoAction.pending) event.preventDefault();
                }}
              >
                <KeyRound size={16} /> Sign in to your business <ArrowRight size={17} />
              </a>
              <p className="field-hint centered">Secure sign-in for your business workspace.</p>
            </div>
          ) : (
            <Notice title="Sign-in isn’t configured yet." tone="warning">
              The service administrator needs to configure authentication before you can create or
              open a workspace.
            </Notice>
          )}
          <p className="auth-fine-print">
            <ShieldCheck size={14} /> Each business has its own workspace and access permissions.
          </p>
        </div>
      </div>
    </div>
  );
}

export function SetupPage() {
  const { session, refresh, logout } = useAuth();
  const [name, setName] = useState('');
  const [profile, setProfile] = useState<'merchandise' | 'bakery'>('merchandise');
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  );
  const [deposit, setDeposit] = useState('50');
  const [demo, setDemo] = useState(false);
  const action = useAction();
  const signOut = useAction();
  const navigate = useNavigate();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () =>
        post<Workspace>('/workspaces', {
          name,
          profile,
          currency: 'USD',
          timezone,
          deposit_bps: Math.round(Number(deposit) * 100),
          seed_demo: demo,
        }),
      async (workspace) => {
        await refresh();
        navigate(`/w/${workspace.id}/decisions`);
      },
    );
  };
  return (
    <>
      <RuntimeBar />
      <div className="setup-page">
        <header className="setup-header">
          <Brand />
          <nav className="account-navigation" aria-label="Account navigation">
            {Boolean(session?.workspaces.length) && (
              <Link className="subtle-link" to="/">
                Back to workspace
              </Link>
            )}
            {session?.user.is_platform_admin && (
              <Link className="subtle-link" to="/operations">
                <ShieldCheck size={15} /> Operations
              </Link>
            )}
            <Button variant="ghost" busy={signOut.pending} onClick={() => void signOut.run(logout)}>
              <LogOut size={15} /> Sign out
            </Button>
          </nav>
        </header>
        {signOut.error && (
          <div className="section-gap">
            <ErrorNotice message={signOut.error} />
          </div>
        )}
        <div className="setup-intro">
          <p className="eyebrow">Make it your own</p>
          <h1>
            Your business.
            <br />
            Your operating rules.
          </h1>
          <p>
            A few details give every request a reliable starting point. You can review your stock,
            capacity, and rate card in the workspace.
          </p>
        </div>
        <form className="setup-grid" onSubmit={submit}>
          <section className="panel panel-padded form-stack">
            <div className="section-title">
              <span className="step-number">1</span>
              <h2>The basics</h2>
            </div>
            <Field label="Business name" htmlFor="business-name">
              <input
                id="business-name"
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                maxLength={120}
                placeholder="Your studio, shop, or bakery"
              />
            </Field>
            <fieldset className="profile-picker">
              <legend>What do you make?</legend>
              {(
                [
                  {
                    value: 'merchandise',
                    title: 'Merchandise',
                    text: 'Products, sizes, artwork and print capacity.',
                  },
                  {
                    value: 'bakery',
                    title: 'Bakery',
                    text: 'Recipes, decoration and preparation time.',
                  },
                ] as const
              ).map((item) => (
                <label
                  key={item.value}
                  className={`profile-option ${profile === item.value ? 'selected' : ''}`}
                >
                  <input
                    type="radio"
                    name="profile"
                    checked={profile === item.value}
                    onChange={() => setProfile(item.value)}
                  />
                  <span>
                    <strong>{item.title}</strong>
                    <small>{item.text}</small>
                  </span>
                </label>
              ))}
            </fieldset>
            <div className="form-row">
              <Field
                label="Business timezone"
                htmlFor="timezone"
                hint="Pickup commitments use this timezone."
              >
                <input
                  id="timezone"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  required
                  placeholder="America/New_York"
                />
              </Field>
              <Field label="Currency" htmlFor="currency">
                <select id="currency" disabled value="USD">
                  <option value="USD">USD · US dollar</option>
                </select>
              </Field>
            </div>
            <Field
              label="Deposit before production"
              htmlFor="deposit-percent"
              hint="Existing deposits count toward a revised order’s requirement."
            >
              <div className="input-affix">
                <input
                  id="deposit-percent"
                  type="number"
                  min="0"
                  max="100"
                  step="1"
                  value={deposit}
                  onChange={(e) => setDeposit(e.target.value)}
                  required
                />
                <span>%</span>
              </div>
            </Field>
          </section>
          <aside className="setup-aside">
            <section className="panel panel-padded">
              <div className="section-title">
                <span className="step-number">2</span>
                <h2>A considered starting point</h2>
              </div>
              <div className="setup-rule">
                <Check size={17} />
                <div>
                  <strong>Customer requests stay distinct from consent.</strong>
                  <p>Changed terms go back to the customer for review.</p>
                </div>
              </div>
              <div className="setup-rule">
                <Check size={17} />
                <div>
                  <strong>Resources are checked before commitment.</strong>
                  <p>Prices and availability come from business rules.</p>
                </div>
              </div>
              <div className="setup-rule">
                <Check size={17} />
                <div>
                  <strong>Work starts when the conditions are met.</strong>
                  <p>Approval, resources, deposit, and holds are checked together.</p>
                </div>
              </div>
              <label className="checkbox-card">
                <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
                <span>
                  <strong>Include a sample order</strong>
                  <small>
                    Add clearly marked sample stock, prices, and a challenging change request to
                    explore the workflow.
                  </small>
                </span>
              </label>
              {demo && <Badge tone="amber">Sample data will stay labeled</Badge>}
            </section>
            <ErrorNotice message={action.error} />
            <Button type="submit" variant="primary" busy={action.pending} className="full-width">
              Create workspace <ArrowRight size={17} />
            </Button>
            <p className="field-hint">
              Review your rate card and resources before making customer commitments.
            </p>
          </aside>
        </form>
      </div>
    </>
  );
}

import {
  ArrowLeft,
  ArrowRight,
  ClipboardList,
  Inbox,
  LayoutDashboard,
  LogOut,
  PackageCheck,
  Plus,
  Settings2,
  ShieldCheck,
  Users,
  Warehouse,
} from 'lucide-react';
import { useEffect } from 'react';
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { useAction, useApi } from '../lib/hooks';
import { initials } from '../lib/format';
import { WorkspaceContext, useWorkspace } from '../lib/workspace';
import { Badge, Brand, Button, EmptyState, ErrorNotice, Loading } from './ui';
import { DemoGuide } from './demo-guide';

export function RuntimeBar() {
  const { config } = useAuth();
  const { data: runtime } = useApi<{
    agent_mode: 'reference' | 'bedrock';
    live_ai_configured: boolean;
    environment: string;
  }>('/runtime');
  if (config?.auth_mode !== 'development' && runtime?.agent_mode !== 'reference') return null;
  return (
    <div className="runtime-bar">
      <span className="runtime-dot" />
      {config?.auth_mode === 'development' && <strong>Development sign-in</strong>}
      {runtime?.agent_mode === 'reference' && (
        <>
          <span className="runtime-divider" />
          <strong>Reference analysis</strong>
          <span>Live AI is not connected. Actions use the saved server rules.</span>
        </>
      )}
    </div>
  );
}

export function SessionGuard() {
  const { session, loading, error, refresh } = useAuth();
  const location = useLocation();
  if (loading) return <Loading label="Opening your workspace…" />;
  if (error && !session)
    return (
      <div className="standalone">
        <Brand />
        <ErrorNotice message={error} retry={() => void refresh()} />
      </div>
    );
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}

export function Home() {
  const { session, loading } = useAuth();
  if (loading) return <Loading />;
  if (!session) return <Navigate to="/login" replace />;
  if (!session.workspaces.length && session.user.is_platform_admin)
    return <Navigate to="/operations" replace />;
  return (
    <Navigate
      to={
        session.workspaces[0]
          ? `/w/${session.workspaces[0].id}/${session.workspaces[0].role === 'operator' ? 'production' : 'decisions'}`
          : '/setup'
      }
      replace
    />
  );
}

export function OwnerGuard() {
  const workspace = useWorkspace();
  return workspace.role === 'owner' ? (
    <Outlet />
  ) : (
    <Navigate to={`/w/${workspace.id}/production`} replace />
  );
}

export function BusinessAccountGuard() {
  const { session, logout } = useAuth();
  const location = useLocation();
  const action = useAction();
  if (session?.auth_method !== 'demo') return <Outlet />;
  return (
    <div className={location.pathname === '/setup' ? 'standalone' : undefined}>
      <EmptyState
        title="Make it your own with a business account."
        action={
          <div className="demo-restricted-actions">
            <Link className="button button-primary" to="/">
              Back to the demo
            </Link>
            <Button busy={action.pending} onClick={() => void action.run(logout)}>
              End demo &amp; sign in
            </Button>
          </div>
        }
      >
        The demo includes two prepared businesses. Creating orders, managing a team, and changing
        business rules are available in a business account.
      </EmptyState>
      <ErrorNotice message={action.error} />
    </div>
  );
}

export function AppLayout() {
  const { workspaceId } = useParams();
  const { session, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const signOut = useAction();
  const workspace = session?.workspaces.find((item) => item.id === workspaceId);
  const base = `/w/${workspaceId}`;
  const segment = location.pathname.split('/')[3] || 'decisions';
  const labels: Record<string, string> = {
    decisions: 'Decisions',
    orders: 'Orders',
    production: 'Production',
    resources: 'Capacity & stock',
    settings: 'Business rules',
    team: 'Team',
  };

  useEffect(() => {
    document.title = `${labels[segment] ?? 'Workspace'} · OrderToWork`;
    document.querySelector<HTMLElement>('main h1')?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [location.pathname]);

  if (!workspace)
    return (
      <div className="standalone">
        <Brand />
        <EmptyState
          title="This workspace isn’t available to you."
          action={
            <Link className="button button-primary" to="/">
              Open my workspace <ArrowRight size={16} />
            </Link>
          }
        >
          Your account may not be a member, or access may have changed.
        </EmptyState>
      </div>
    );
  const owner = workspace.role === 'owner';
  const demo = session?.auth_method === 'demo';
  const nav = [
    ...(owner
      ? [
          { path: 'decisions', label: 'Decisions', icon: Inbox },
          { path: 'orders', label: 'Orders', icon: ClipboardList },
        ]
      : []),
    { path: 'production', label: 'Production', icon: PackageCheck },
    ...(owner
      ? [
          { path: 'resources', label: 'Capacity & stock', icon: Warehouse },
          ...(!demo
            ? [
                { path: 'settings', label: 'Business rules', icon: Settings2 },
                { path: 'team', label: 'Team', icon: Users },
              ]
            : []),
        ]
      : []),
  ];
  return (
    <WorkspaceContext.Provider value={workspace}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <RuntimeBar />
      <div className="app-shell">
        <aside className="sidebar">
          <Link
            to={base + (owner ? '/decisions' : '/production')}
            className="brand-link"
            aria-label="OrderToWork home"
          >
            <Brand />
          </Link>
          <div className="workspace-switch">
            <label htmlFor="workspace-switch">
              {demo ? 'Explore another business' : 'Your workspace'}
            </label>
            <select
              id="workspace-switch"
              value={workspace.id}
              onChange={(e) => {
                const next = session?.workspaces.find((item) => item.id === e.target.value);
                navigate(
                  `/w/${e.target.value}/${next?.role === 'operator' ? 'production' : 'decisions'}`,
                );
              }}
            >
              {session?.workspaces.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            <span>
              {workspace.profile === 'merchandise'
                ? 'Made-to-order merchandise'
                : 'Made-to-order bakery'}
            </span>
          </div>
          <nav className="main-nav" aria-label="Workspace navigation">
            {nav.map(({ path, label, icon: Icon }) => (
              <NavLink
                key={path}
                to={`${base}/${path}`}
                className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
              >
                <Icon size={18} strokeWidth={1.7} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-bottom">
            {demo ? (
              <p className="demo-sidebar-note">
                Your own sample workspace. Changes stay here and expire with this session.
              </p>
            ) : (
              <Link to="/setup" className="subtle-link">
                <Plus size={15} /> Add a workspace
              </Link>
            )}
            {session?.user.is_platform_admin && (
              <Link to="/operations" className="subtle-link">
                <ShieldCheck size={15} /> Platform operations
              </Link>
            )}
            <div className="account-row">
              <div className="avatar">{initials(session?.user.name || 'You')}</div>
              <div className="account-name">
                <strong>{session?.user.name}</strong>
                <span>
                  {demo ? 'Demo visitor' : owner ? 'Workspace owner' : 'Production operator'}
                </span>
              </div>
              <button
                className="icon-button"
                title={demo ? 'End demo' : 'Sign out'}
                aria-label={demo ? 'End demo' : 'Sign out'}
                onClick={() => void signOut.run(logout)}
                disabled={signOut.pending}
              >
                <LogOut size={16} />
              </button>
            </div>
            <ErrorNotice message={signOut.error} />
          </div>
        </aside>
        <div className="app-content">
          <header className="topbar">
            <div className="breadcrumb">
              <span>Workspace</span>
              <span aria-hidden="true">/</span>
              <strong>{labels[segment] ?? 'Order'}</strong>
            </div>
            <div className="topbar-actions">
              <span className="timezone-label">{workspace.timezone.replaceAll('_', ' ')}</span>
              <Badge>{demo ? 'Demo visitor' : owner ? 'Owner' : 'Operator'}</Badge>
              {owner && !demo && (
                <Link className="button button-small button-secondary" to={`${base}/orders/new`}>
                  <Plus size={15} /> New order
                </Link>
              )}
              <details className="mobile-account">
                <summary aria-label="Account menu">
                  <span className="avatar">{initials(session?.user.name || 'You')}</span>
                </summary>
                <div className="mobile-account-menu">
                  <strong>{session?.user.name}</strong>
                  <small>{demo ? 'Private demo session' : session?.user.email}</small>
                  {!demo && (
                    <Link to="/setup">
                      <Plus size={15} /> Add a workspace
                    </Link>
                  )}
                  {session?.user.is_platform_admin && (
                    <Link to="/operations">
                      <ShieldCheck size={15} /> Platform operations
                    </Link>
                  )}
                  <button disabled={signOut.pending} onClick={() => void signOut.run(logout)}>
                    <LogOut size={15} /> {demo ? 'End demo' : 'Sign out'}
                  </button>
                  <ErrorNotice message={signOut.error} />
                </div>
              </details>
            </div>
          </header>
          <main id="main" className="main-content" tabIndex={-1}>
            <DemoGuide />
            <Outlet />
          </main>
        </div>
      </div>
    </WorkspaceContext.Provider>
  );
}

export function OperationsLayout() {
  const { session, logout } = useAuth();
  const signOut = useAction();
  if (!session?.user.is_platform_admin)
    return (
      <div className="standalone">
        <EmptyState
          title="Platform access is restricted."
          action={
            <Link to="/" className="button button-primary">
              Return to workspace
            </Link>
          }
        >
          Only an authorized platform administrator can open this page.
        </EmptyState>
      </div>
    );
  const hasWorkspace = session.workspaces.length > 0;
  return (
    <>
      <RuntimeBar />
      <div className="operations-header">
        <Link to="/" className="brand-link">
          <Brand />
        </Link>
        <nav className="account-navigation" aria-label="Account navigation">
          <Link to={hasWorkspace ? '/' : '/setup'} className="subtle-link">
            {hasWorkspace ? <ArrowLeft size={16} /> : <Plus size={16} />}
            {hasWorkspace ? 'Back to workspace' : 'Create workspace'}
          </Link>
          <Button variant="ghost" busy={signOut.pending} onClick={() => void signOut.run(logout)}>
            <LogOut size={15} /> Sign out
          </Button>
        </nav>
      </div>
      <main className="main-content operations-content">
        <ErrorNotice message={signOut.error} />
        <div className="operations-kicker">
          <LayoutDashboard size={15} /> Platform operations
        </div>
        <Outlet />
      </main>
    </>
  );
}

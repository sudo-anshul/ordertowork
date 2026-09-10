import { Activity, RefreshCw, ShieldCheck } from 'lucide-react';
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Loading,
  Notice,
  PageIntro,
  Panel,
  PanelHeading,
} from '../components/ui';
import { shortDate, titleCase } from '../lib/format';
import { useApi } from '../lib/hooks';

interface Overview {
  counts: { users: number; workspaces: number; memberships: number; active_sessions: number };
  workspaces: {
    id: string;
    name: string;
    profile: string;
    status: string;
    created_at?: string;
    is_demo?: boolean;
  }[];
  recent_audit: {
    id: string;
    action?: string;
    event?: string;
    type?: string;
    actor_id?: string;
    actor_user_id?: string;
    workspace_id?: string;
    created_at: string;
  }[];
  jobs?: { queued: number; running: number; succeeded: number; failed: number };
  recent_failed_jobs?: {
    id: string;
    workspace_id: string;
    status: string;
    error: string | null;
    updated_at: string;
  }[];
}
export function OperationsPage() {
  const { data, loading, error, refresh, refreshing } = useApi<Overview>('/platform/overview');
  return (
    <>
      <PageIntro
        eyebrow="Service overview"
        title="Keep the service running clearly."
        actions={
          <Button onClick={() => void refresh()} busy={refreshing}>
            <RefreshCw size={15} /> Refresh
          </Button>
        }
      >
        <p>Account and operational metadata for authorized support administrators.</p>
      </PageIntro>
      <Notice title="Support access is deliberately limited.">
        <p>
          Customer messages, order specifications, and payment records are not browsable from this
          view. Business permissions stay separate from platform administration.
        </p>
      </Notice>
      <ErrorNotice message={error} retry={() => void refresh()} />
      {loading ? (
        <Loading label="Loading platform metadata…" />
      ) : (
        data && (
          <>
            <div className="metric-grid">
              {(
                [
                  { key: 'users', label: 'Accounts' },
                  { key: 'workspaces', label: 'Workspaces' },
                  { key: 'memberships', label: 'Memberships' },
                  { key: 'active_sessions', label: 'Active sessions' },
                ] as const
              ).map((item) => (
                <div className="metric-card" key={item.key}>
                  <label>{item.label}</label>
                  <strong>{data.counts[item.key]}</strong>
                </div>
              ))}
            </div>
            <div className="settings-grid">
              <Panel className="settings-wide">
                <PanelHeading title="Business accounts">
                  <ShieldCheck size={18} />
                </PanelHeading>
                {data.workspaces.length ? (
                  <div className="table-overflow">
                    <table className="resource-table">
                      <thead>
                        <tr>
                          <th>Workspace</th>
                          <th>Profile</th>
                          <th>Status</th>
                          <th>Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.workspaces.map((workspace) => (
                          <tr key={workspace.id}>
                            <td>
                              <strong>{workspace.name}</strong>
                              <small className="mono">{workspace.id}</small>
                            </td>
                            <td>
                              {titleCase(workspace.profile)}
                              {workspace.is_demo && <Badge tone="amber">Sample</Badge>}
                            </td>
                            <td>
                              <Badge tone={workspace.status === 'active' ? 'green' : 'neutral'}>
                                {titleCase(workspace.status)}
                              </Badge>
                            </td>
                            <td>{workspace.created_at ? shortDate(workspace.created_at) : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyState title="No business accounts yet.">
                    Accounts will appear after workspace creation.
                  </EmptyState>
                )}
              </Panel>
              {data.jobs && (
                <Panel>
                  <PanelHeading title="Analysis runs">
                    <Activity size={17} />
                  </PanelHeading>
                  {Object.entries(data.jobs).map(([status, count]) => (
                    <div className="key-value" key={status}>
                      <span>{titleCase(status)}</span>
                      <strong>{count}</strong>
                    </div>
                  ))}
                </Panel>
              )}
              {data.recent_failed_jobs && (
                <Panel>
                  <PanelHeading title="Failed analysis runs">
                    <Badge tone="amber">{data.recent_failed_jobs.length}</Badge>
                  </PanelHeading>
                  {data.recent_failed_jobs.length ? (
                    data.recent_failed_jobs.map((job) => (
                      <div className="audit-row" key={job.id}>
                        <div>
                          <strong className="mono">{job.id.slice(0, 12)}</strong>
                          <p>{job.error || 'No failure detail recorded.'}</p>
                          <p>Workspace {job.workspace_id.slice(0, 12)}</p>
                        </div>
                        <time>{shortDate(job.updated_at)}</time>
                      </div>
                    ))
                  ) : (
                    <p className="settings-description">No recent failures reported.</p>
                  )}
                </Panel>
              )}
              <Panel className="settings-wide">
                <PanelHeading title="Recent access events">
                  <Badge>Metadata only</Badge>
                </PanelHeading>
                {data.recent_audit.length ? (
                  data.recent_audit.map((event) => (
                    <div className="audit-row" key={event.id}>
                      <div>
                        <strong>
                          {titleCase(event.action ?? event.event ?? event.type ?? 'Account event')}
                        </strong>
                        <p>
                          {event.workspace_id
                            ? `Workspace ${event.workspace_id.slice(0, 12)}`
                            : 'Account-level event'}
                        </p>
                      </div>
                      <time>{shortDate(event.created_at)}</time>
                    </div>
                  ))
                ) : (
                  <p className="settings-description">No access events have been recorded.</p>
                )}
              </Panel>
            </div>
          </>
        )
      )}
    </>
  );
}

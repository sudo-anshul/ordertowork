import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  Clipboard,
  Copy,
  Download,
  ExternalLink,
  Link2,
  LoaderCircle,
  MessageSquareText,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useWorkspace } from '../lib/workspace';
import { Attachments } from '../components/attachments';
import { AnalysisEvidence } from '../components/analysis-evidence';
import { OrderForm } from '../components/order-form';
import { OrderBadge, Readiness, Specification, TermsDiff, sizesText } from '../components/orders';
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  KeyValue,
  Loading,
  Modal,
  Notice,
  PageIntro,
  Panel,
  PanelHeading,
} from '../components/ui';
import { errorMessage, newIdempotencyKey, post } from '../lib/api';
import { dateTime, initials, money, shortDate, titleCase } from '../lib/format';
import { useAction, useApi } from '../lib/hooks';
import type {
  Analysis,
  AnalysisJob,
  OrderDetail,
  ResourcesResponse,
  Revision,
  ShareResult,
  Ticket,
} from '../lib/types';

export function OrderDetailPage() {
  const workspace = useWorkspace();
  const { orderId } = useParams();
  const path = `/workspaces/${workspace.id}/orders/${orderId}`;
  const {
    data: order,
    loading,
    error,
    refresh,
    setData,
  } = useApi<OrderDetail>(path, { pollMs: 5000 });
  const resources = useApi<ResourcesResponse>(`/workspaces/${workspace.id}/resources`);
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') ?? 'review';
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [composer, setComposer] = useState(false);
  const [proposal, setProposal] = useState(false);
  const [deposit, setDeposit] = useState(false);
  const [hold, setHold] = useState(false);
  const [shareResult, setShareResult] = useState<ShareResult | null>(null);
  const [latestJob, setLatestJob] = useState<AnalysisJob | null>(null);
  const action = useAction();
  const owner = workspace.role === 'owner';
  const candidates = (order?.revisions ?? []).filter((revision) =>
    ['proposed', 'shared'].includes(revision.status),
  );
  const selected =
    candidates.find((revision) => revision.id === selectedId) ??
    candidates.find((revision) => revision.id === order?.shared_revision_id) ??
    candidates.find((revision) => revision.feasible) ??
    candidates[0];
  const activeJob = order?.latest_job ?? latestJob;

  useEffect(() => {
    setSelectedId(null);
    setLatestJob(null);
    setShareResult(null);
  }, [orderId]);

  if (loading) return <Loading label="Opening the order and its current agreement…" />;
  if (!order)
    return (
      <ErrorNotice
        title="This order could not be opened."
        message={error || 'It may no longer be available to this workspace.'}
        retry={() => void refresh()}
      />
    );
  const acceptUpdate = async (next: OrderDetail) => {
    setData(next);
    await refresh(true);
  };
  const needsDecision =
    candidates.length > 0 ||
    ['needs_review', 'needs_decision', 'needs_clarification', 'new'].includes(order.status);
  const heading = order.hold_reason
    ? 'A decision before work moves forward.'
    : order.status === 'awaiting_approval'
      ? 'The customer has the next decision.'
      : needsDecision
        ? order.accepted_revision
          ? 'Find a workable way forward.'
          : 'Make the first agreement clear.'
        : order.production_status === 'started'
          ? 'The team is working from the agreement.'
          : order.status === 'deposit_due'
            ? 'Agreed. One step before production.'
            : order.production_ready
              ? 'Ready to hand off.'
              : order.accepted_revision
                ? 'Review the current agreement.'
                : 'Make the first agreement clear.';

  return (
    <>
      <PageIntro
        eyebrow={`${order.number} · ${order.customer_name}`}
        title={heading}
        actions={
          <Link className="button button-secondary" to={`/w/${workspace.id}/decisions`}>
            <ArrowLeft size={16} /> Back to decisions
          </Link>
        }
      >
        <div className="order-intro-meta">
          <OrderBadge status={order.status} />
          {order.is_demo && <Badge tone="amber">Sample order</Badge>}
          <span>
            {order.accepted_revision
              ? `Revision ${order.accepted_revision.number} is the accepted commitment.`
              : 'No customer-approved commitment yet.'}
          </span>
        </div>
      </PageIntro>
      <ErrorNotice
        message={error || action.error}
        retry={error ? () => void refresh() : undefined}
      />
      <div className="order-tabs" role="navigation" aria-label="Order sections">
        {[
          { id: 'review', label: 'Order & proposals' },
          { id: 'messages', label: `Messages (${order.messages.length})` },
          { id: 'production', label: 'Production' },
          { id: 'history', label: 'History' },
        ].map((item) => (
          <button
            key={item.id}
            className={tab === item.id ? 'active' : ''}
            aria-current={tab === item.id ? 'page' : undefined}
            onClick={() => setParams(item.id === 'review' ? {} : { tab: item.id })}
          >
            {item.label}
          </button>
        ))}
        {owner && (
          <Button variant="ghost" onClick={() => setComposer(true)}>
            <Plus size={15} /> Add customer message
          </Button>
        )}
      </div>
      {activeJob && (
        <AnalysisStatus
          job={activeJob}
          analysis={order.latest_analysis}
          workspaceId={workspace.id}
          owner={owner}
          onComplete={() => void refresh(true)}
        />
      )}
      {tab === 'review' && (
        <div className="review-grid">
          <div className="review-main">
            {order.hold_reason && (
              <Notice tone="warning" title="Production is on hold.">
                <p>{order.hold_reason}</p>
                {owner && (
                  <Button variant="ghost" onClick={() => setHold(true)}>
                    Review this hold <ArrowRight size={14} />
                  </Button>
                )}
              </Notice>
            )}
            {Boolean(order.latest_analysis?.missing_fields?.length) && (
              <Notice tone="warning" title="A few details still need clarification.">
                <ul>
                  {order.latest_analysis?.missing_fields.map((field) => (
                    <li key={field}>{titleCase(field)}</li>
                  ))}
                </ul>
                {owner && (
                  <Button variant="ghost" onClick={() => setComposer(true)}>
                    Add the customer’s clarification <ArrowRight size={14} />
                  </Button>
                )}
              </Notice>
            )}
            <Panel className="proposal-panel">
              <div className="panel-padded">
                {order.messages.length > 0 && (
                  <SourceMessage message={order.messages.at(-1)!} customer={order.customer_name} />
                )}
                <PanelHeading
                  title={
                    selected
                      ? order.accepted_revision
                        ? 'A clear view of the proposed change'
                        : 'The proposed order'
                      : 'Current accepted order'
                  }
                >
                  {selected ? (
                    <Badge>
                      Revision {selected.number} · {titleCase(selected.status)}
                    </Badge>
                  ) : (
                    order.accepted_revision && (
                      <Badge tone="green">
                        Revision {order.accepted_revision.number} · accepted
                      </Badge>
                    )
                  )}
                </PanelHeading>
                {selected ? (
                  <>
                    <TermsDiff
                      before={order.accepted_revision?.terms}
                      after={selected.terms}
                      timezone={workspace.timezone}
                    />
                    <Specification terms={selected.terms} />
                    {selected.terms.rush_fee_cents > 0 && (
                      <p className="price-explanation">
                        Total includes a {money(selected.terms.rush_fee_cents, workspace.currency)}{' '}
                        configured rush fee.
                      </p>
                    )}
                  </>
                ) : order.accepted_revision ? (
                  <>
                    <TermsDiff
                      after={order.accepted_revision.terms}
                      timezone={workspace.timezone}
                    />
                    <Specification terms={order.accepted_revision.terms} />
                  </>
                ) : (
                  <EmptyState
                    title="There’s no proposal to review yet."
                    action={
                      owner ? (
                        <Button onClick={() => setProposal(true)}>
                          <Plus size={16} /> Prepare a proposal
                        </Button>
                      ) : undefined
                    }
                  >
                    Add the customer’s request or prepare a proposal from the business rules.
                  </EmptyState>
                )}
                {candidates.length > 0 && (
                  <div className="section-gap">
                    <PanelHeading
                      title={
                        candidates.length > 1 ? 'Choose a workable option' : 'Proposal to share'
                      }
                    >
                      <span className="quiet-label">Customer consent is required</span>
                    </PanelHeading>
                    <fieldset className="revision-options">
                      <legend className="sr-only">Select a revision to offer the customer</legend>
                      {candidates.map((revision) => (
                        <label
                          key={revision.id}
                          className={`revision-option ${selected?.id === revision.id ? 'selected' : ''} ${!revision.feasible ? 'infeasible' : ''}`}
                        >
                          <input
                            type="radio"
                            name="revision"
                            checked={selected?.id === revision.id}
                            onChange={() => setSelectedId(revision.id)}
                          />
                          <span className="revision-option-body">
                            <strong>{revision.label || `Revision ${revision.number}`}</strong>
                            <span>
                              {revision.terms.quantity} {revision.terms.product_name.toLowerCase()}{' '}
                              · {titleCase(revision.terms.variant)}
                            </span>
                            <span>{dateTime(revision.terms.pickup_at, workspace.timezone)}</span>
                            {!revision.feasible && (
                              <span className="inline-warning">
                                <AlertCircle size={13} /> Not feasible with current resources
                              </span>
                            )}
                            {revision.status === 'shared' && (
                              <span className="option-shared">Shared with customer</span>
                            )}
                          </span>
                          <span className="revision-option-price">
                            {money(revision.terms.total_cents, workspace.currency)}
                            <small>
                              {money(
                                Math.max(
                                  0,
                                  revision.terms.required_deposit_cents - order.deposit_paid_cents,
                                ),
                                workspace.currency,
                              )}{' '}
                              deposit top-up
                            </small>
                          </span>
                        </label>
                      ))}
                    </fieldset>
                  </div>
                )}
              </div>
              {owner && (
                <div className="panel-action-footer">
                  <p>
                    {selected?.status === 'shared'
                      ? 'Creating a replacement link invalidates the earlier link. No message is sent automatically.'
                      : 'Sharing creates a customer review link. It does not reserve new resources or take payment.'}
                  </p>
                  <div className="action-cluster">
                    <Button
                      onClick={() => setProposal(true)}
                      disabled={order.production_status === 'started'}
                    >
                      <Plus size={16} /> Another option
                    </Button>
                    {selected && (
                      <Button
                        variant="primary"
                        disabled={!selected.feasible || order.production_status === 'started'}
                        busy={action.pending}
                        onClick={() =>
                          void action.run(
                            () => post<ShareResult>(`${path}/proposals/${selected.id}/share`),
                            async (result) => {
                              setShareResult(result);
                              await refresh(true);
                            },
                          )
                        }
                      >
                        <Link2 size={16} />{' '}
                        {selected.status === 'shared'
                          ? 'Create replacement link'
                          : `Share revision ${selected.number}`}
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </Panel>
            {order.shared_revision_id && !shareResult && (
              <Notice title="A customer review link is active.">
                The proposal is waiting for a customer response. For privacy, an existing link
                cannot be retrieved; creating a replacement link revokes the old one.
              </Notice>
            )}
          </div>
          <aside className="order-aside">
            {Boolean(
              order.latest_analysis?.requested_issues?.length || selected?.issues.length,
            ) && (
              <Panel className="panel-padded">
                <p className="eyebrow">Why the request needs a choice</p>
                <div className="constraint-list">
                  {(order.latest_analysis?.requested_issues?.length
                    ? order.latest_analysis.requested_issues
                    : (selected?.issues ?? [])
                  ).map((issue, index) => (
                    <div className="constraint" key={`${issue.code}-${index}`}>
                      <span className="constraint-icon">
                        <AlertCircle size={16} />
                      </span>
                      <div>
                        <strong>{titleCase(issue.code)}</strong>
                        <p>{issue.message}</p>
                        {issue.required != null && issue.available != null && (
                          <p className="constraint-figures">
                            {issue.required} needed · {issue.available} available
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
            )}
            <Panel className="panel-padded">
              <p className="eyebrow">Before production</p>
              <Readiness order={order} />
              <div className="aside-actions">
                {owner && order.accepted_revision && (
                  <Button onClick={() => setDeposit(true)} className="full-width">
                    Record received deposit
                  </Button>
                )}
                <Button
                  variant={order.production_ready ? 'primary' : 'secondary'}
                  onClick={() => setParams({ tab: 'production' })}
                  className="full-width"
                >
                  {order.production_ready
                    ? 'View accepted work order'
                    : 'View production conditions'}{' '}
                  <ArrowRight size={16} />
                </Button>
              </div>
            </Panel>
            <Panel className="panel-padded">
              <p className="eyebrow">Financial record</p>
              <dl>
                <KeyValue label="Deposit received">
                  {money(order.deposit_paid_cents, workspace.currency)}
                </KeyValue>
                {order.accepted_revision && (
                  <>
                    <KeyValue label="Accepted total">
                      {money(order.accepted_revision.terms.total_cents, workspace.currency)}
                    </KeyValue>
                    <KeyValue label="Balance remaining">
                      {money(
                        Math.max(
                          0,
                          order.accepted_revision.terms.total_cents - order.deposit_paid_cents,
                        ),
                        workspace.currency,
                      )}
                    </KeyValue>
                  </>
                )}
              </dl>
            </Panel>
            <p className="aside-note">
              <ShieldCheck size={15} /> Availability is checked again when the customer approves. A
              failed change preserves the original commitment.
            </p>
          </aside>
        </div>
      )}
      {tab === 'messages' && (
        <Panel className="panel-padded">
          <PanelHeading title="Customer source messages">
            {owner && (
              <Button onClick={() => setComposer(true)}>
                <Plus size={16} /> Add message
              </Button>
            )}
          </PanelHeading>
          {order.messages.length ? (
            <div className="messages-list">
              {[...order.messages].reverse().map((message) => (
                <SourceMessage
                  key={message.id}
                  message={message}
                  customer={order.customer_name}
                  expanded
                />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<MessageSquareText size={28} />}
              title="The conversation starts with a source."
            >
              Paste the customer’s actual words. The agent will check the request against this order
              and the saved business rules.
            </EmptyState>
          )}
        </Panel>
      )}
      {tab === 'messages' && owner && <Attachments orderPath={path} />}
      {tab === 'history' && (
        <Panel className="panel-padded">
          <PanelHeading title="What happened, and when">
            <Badge>{order.events.length} events</Badge>
          </PanelHeading>
          {order.events.length ? (
            <ol className="event-timeline">
              {order.events.map((event) => (
                <li key={event.id}>
                  <span className="event-dot" />
                  <div>
                    <strong>{event.message || titleCase(event.type)}</strong>
                    <time dateTime={event.created_at}>{shortDate(event.created_at)}</time>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState title="No events recorded yet.">
              Saved order events will appear here.
            </EmptyState>
          )}
          <details className="revision-history">
            <summary>
              Earlier revisions <ChevronDown size={15} />
            </summary>
            {order.revisions.map((revision) => (
              <div className="revision-history-row" key={revision.id}>
                <span>
                  Revision {revision.number}
                  <small>{revision.label}</small>
                </span>
                <Badge>{titleCase(revision.status)}</Badge>
                <strong>{money(revision.terms.total_cents, workspace.currency)}</strong>
              </div>
            ))}
          </details>
        </Panel>
      )}
      {tab === 'production' && (
        <ProductionView
          order={order}
          path={path}
          onUpdate={acceptUpdate}
          onDeposit={() => setDeposit(true)}
          onHold={() => setHold(true)}
        />
      )}
      {composer && (
        <MessageComposer
          path={path}
          onClose={() => setComposer(false)}
          onSaved={async (job) => {
            setLatestJob(job);
            setComposer(false);
            await refresh(true);
          }}
        />
      )}
      {proposal && (
        <Modal title="Prepare another option" onClose={() => setProposal(false)} wide>
          {resources.loading ? (
            <Loading label="Loading the rate card…" />
          ) : resources.data?.products.length ? (
            <div className="modal-body">
              <OrderForm
                products={resources.data.products}
                timezone={workspace.timezone}
                initial={selected?.terms ?? order.accepted_revision?.terms}
                busy={action.pending}
                error={action.error}
                onCancel={() => setProposal(false)}
                onSubmit={(input) =>
                  void action.run(
                    () => post<OrderDetail>(`${path}/proposals`, input),
                    async (next) => {
                      await acceptUpdate(next);
                      setSelectedId(null);
                      setProposal(false);
                    },
                  )
                }
              />
            </div>
          ) : (
            <div className="modal-body">
              <ErrorNotice message={resources.error || 'No products are configured.'} />
            </div>
          )}
        </Modal>
      )}
      {deposit && (
        <DepositModal
          order={order}
          path={path}
          onClose={() => setDeposit(false)}
          onSaved={async (next) => {
            await acceptUpdate(next);
            setDeposit(false);
          }}
        />
      )}
      {hold && (
        <HoldModal
          order={order}
          path={path}
          onClose={() => setHold(false)}
          onSaved={async (next) => {
            await acceptUpdate(next);
            setHold(false);
          }}
        />
      )}
      {shareResult && (
        <ShareModal
          share={shareResult}
          revision={order.revisions.find((revision) => revision.id === shareResult.revision_id)}
          onClose={() => setShareResult(null)}
        />
      )}
    </>
  );
}

function SourceMessage({
  message,
  customer,
  expanded = false,
}: {
  message: { body: string; source: string; created_at: string };
  customer: string;
  expanded?: boolean;
}) {
  return (
    <div className={`source-message ${expanded ? 'source-message-expanded' : ''}`}>
      <div className="source-meta">
        <span className="avatar avatar-small">{initials(customer)}</span>
        <strong>{customer}</strong>
        <span>· {shortDate(message.created_at)}</span>
        <Badge>
          {message.source === 'manual' ? 'Recorded message' : titleCase(message.source)}
        </Badge>
      </div>
      <blockquote>{message.body}</blockquote>
      {!expanded && (
        <details>
          <summary>Why the source matters</summary>
          <p>
            A question or request for pricing does not authorize a change. The original agreement
            remains in place until the customer approves a specific revision and availability is
            checked.
          </p>
        </details>
      )}
    </div>
  );
}

function AnalysisStatus({
  job,
  analysis,
  workspaceId,
  owner,
  onComplete,
}: {
  job: AnalysisJob;
  analysis?: Analysis | null;
  workspaceId: string;
  owner: boolean;
  onComplete: () => void;
}) {
  const path = `/workspaces/${workspaceId}/jobs/${job.id}`;
  const { data, error, loading, refresh } = useApi<AnalysisJob>(path, {
    pollMs: ['queued', 'running'].includes(job.status) ? 1500 : undefined,
  });
  const action = useAction();
  const current = data ?? job;
  const previous = useRef(job.status);
  useEffect(() => {
    if (previous.current !== current.status && ['succeeded', 'failed'].includes(current.status))
      onComplete();
    previous.current = current.status;
  }, [current.status, onComplete]);
  if (!data && loading && !['queued', 'running'].includes(job.status))
    return <Loading compact label="Loading the request check record…" />;
  if (!data && error)
    return (
      <ErrorNotice
        title="The request check record could not be loaded."
        message={error}
        retry={() => void refresh()}
      />
    );
  if (current.status === 'failed')
    return (
      <Notice tone="warning" title="This message needs another check.">
        <p>
          {current.error ||
            current.error_message ||
            'Analysis did not finish. The saved message and accepted order are unchanged.'}
        </p>
        {owner && (
          <Button
            busy={action.pending}
            onClick={() =>
              void action.run(
                () => post<AnalysisJob>(`${path}/retry`),
                async () => {
                  await refresh();
                  onComplete();
                },
              )
            }
          >
            <RefreshCw size={15} /> Retry analysis
          </Button>
        )}
        <ErrorNotice message={action.error} />
      </Notice>
    );
  if (['queued', 'running'].includes(current.status))
    return (
      <div className="analysis-banner" role="status">
        <LoaderCircle size={18} className="spin" />
        <div>
          <strong>
            {current.status === 'queued'
              ? 'The customer’s message is queued.'
              : 'Checking the request against the order and business rules…'}
          </strong>
          <p>The current agreement remains protected while this runs.</p>
        </div>
        {current.mode === 'reference' && <Badge>Reference analysis</Badge>}
        <ErrorNotice message={error} />
      </div>
    );
  return owner ? <AnalysisEvidence job={current} analysis={analysis} /> : null;
}

function MessageComposer({
  path,
  onClose,
  onSaved,
}: {
  path: string;
  onClose: () => void;
  onSaved: (job: AnalysisJob) => Promise<void>;
}) {
  const [body, setBody] = useState('');
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () => post<{ job: AnalysisJob }>(`${path}/messages`, { body: body.trim(), source: 'manual' }),
      (result) => onSaved(result.job),
    );
  };
  return (
    <Modal title="Add a customer message" onClose={onClose}>
      <form className="modal-body form-stack" onSubmit={submit}>
        <p className="muted">
          Paste the customer’s words as they were received. This saves the source and starts an
          analysis of what changed.
        </p>
        <Field
          label="Customer message"
          htmlFor="source-body"
          hint="Recording a message does not send a reply to the customer."
        >
          <textarea
            id="source-body"
            autoFocus
            rows={7}
            required
            maxLength={10000}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="Could we add 15 medium shirts and collect on Thursday at noon instead? Please tell me the price difference before I confirm."
          />
        </Field>
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending} disabled={!body.trim()}>
            <Send size={16} /> Save & analyze request
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ShareModal({
  share,
  revision,
  onClose,
}: {
  share: ShareResult;
  revision?: Revision;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(share.url);
      setCopied(true);
      setCopyError(null);
    } catch {
      input.current?.select();
      setCopyError('Select and copy the link below. Your browser did not allow automatic copying.');
    }
  };
  return (
    <Modal title="The customer review link is ready." onClose={onClose}>
      <div className="modal-body">
        <Notice
          tone="success"
          title={
            revision
              ? `Revision ${revision.number} is ready to review.`
              : 'Proposal shared successfully.'
          }
        >
          Send this link to the customer using your usual channel. No email or message has been sent
          by OrderToWork.
        </Notice>
        <Field
          label="Private customer link"
          htmlFor="share-link"
          hint={`Expires ${dateTime(share.expires_at)}. Anyone with this link can review and respond to this revision.`}
        >
          <div className="copy-field">
            <input
              id="share-link"
              ref={input}
              readOnly
              value={share.url}
              onFocus={(e) => e.target.select()}
            />
            <Button onClick={() => void copy()}>
              <Copy size={16} /> {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </Field>
        <ErrorNotice message={copyError} />
        <div className="form-actions">
          <a
            className="button button-secondary"
            href={share.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open customer view <ExternalLink size={15} />
          </a>
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
        <p className="field-hint">
          Keep the link private. A new share link replaces the previous one.
        </p>
      </div>
    </Modal>
  );
}

function DepositModal({
  order,
  path,
  onClose,
  onSaved,
}: {
  order: OrderDetail;
  path: string;
  onClose: () => void;
  onSaved: (order: OrderDetail) => Promise<void>;
}) {
  const due = Math.max(
    0,
    (order.accepted_revision?.terms.required_deposit_cents ?? 0) - order.deposit_paid_cents,
  );
  const [amount, setAmount] = useState(due ? (due / 100).toFixed(2) : '');
  const [reference, setReference] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const key = useRef(newIdempotencyKey());
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed) return;
    void action.run(
      () =>
        post<OrderDetail>(`${path}/deposits`, {
          amount_cents: Math.round(Number(amount) * 100),
          reference: reference.trim(),
          idempotency_key: key.current,
        }),
      onSaved,
    );
  };
  return (
    <Modal title="Record a received deposit" onClose={onClose}>
      <form className="modal-body form-stack" onSubmit={submit}>
        <Notice title="This records a payment you already received." tone="warning">
          It does not charge the customer or transfer money. Use a bank, cash, or payment-processor
          reference so the record can be reconciled.
        </Notice>
        <div className="form-row">
          <Field
            label={`Amount (${order.accepted_revision?.terms.currency ?? 'USD'})`}
            htmlFor="deposit-amount"
          >
            <input
              id="deposit-amount"
              type="number"
              min="0.01"
              step="0.01"
              required
              value={amount}
              onChange={(e) => {
                setAmount(e.target.value);
                key.current = newIdempotencyKey();
              }}
            />
          </Field>
          <Field label="Already recorded" htmlFor="paid-value">
            <input
              id="paid-value"
              disabled
              value={money(order.deposit_paid_cents, order.accepted_revision?.terms.currency)}
            />
          </Field>
        </div>
        <Field label="Payment reference" htmlFor="deposit-reference">
          <input
            id="deposit-reference"
            value={reference}
            onChange={(e) => {
              setReference(e.target.value);
              key.current = newIdempotencyKey();
            }}
            required
            maxLength={200}
            placeholder="Bank transfer or receipt reference"
          />
        </Field>
        <label className="checkbox-line">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            required
          />
          <span>
            I confirm this money has been received{order.is_demo ? ' for this sample order' : ''}.
          </span>
        </label>
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending} disabled={!confirmed}>
            Record deposit
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function HoldModal({
  order,
  path,
  onClose,
  onSaved,
}: {
  order: OrderDetail;
  path: string;
  onClose: () => void;
  onSaved: (order: OrderDetail) => Promise<void>;
}) {
  const [reason, setReason] = useState(order.hold_reason ?? '');
  const [clear, setClear] = useState(false);
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () => post<OrderDetail>(`${path}/hold`, { reason: clear ? null : reason.trim() }),
      onSaved,
    );
  };
  return (
    <Modal title="Manage the production hold" onClose={onClose}>
      <form className="modal-body form-stack" onSubmit={submit}>
        <p className="muted">
          A hold prevents work from starting. Clearing it does not skip customer approval,
          reservations, or the deposit requirement.
        </p>
        <Field label="Reason for the hold" htmlFor="hold-reason">
          <textarea
            id="hold-reason"
            rows={3}
            required={!clear}
            disabled={clear}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        {order.hold_reason && (
          <label className="checkbox-line">
            <input type="checkbox" checked={clear} onChange={(e) => setClear(e.target.checked)} />
            <span>The issue is resolved. Clear this hold.</span>
          </label>
        )}
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button onClick={onClose} type="button">
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending}>
            {clear ? 'Clear hold' : 'Save hold'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ProductionView({
  order,
  path,
  onUpdate,
  onDeposit,
  onHold,
}: {
  order: OrderDetail;
  path: string;
  onUpdate: (order: OrderDetail) => Promise<void>;
  onDeposit: () => void;
  onHold: () => void;
}) {
  const workspace = useWorkspace();
  const ticket = useApi<Ticket>(
    order.production_ready || order.production_status === 'started' ? `${path}/ticket` : null,
  );
  const action = useAction();
  const [startConfirm, setStartConfirm] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const download = () => {
    if (!ticket.data) return;
    try {
      const value = ticket.data;
      const revision = typeof value.revision === 'number' ? value.revision : value.revision.number;
      const lines = [
        workspace.name,
        `WORK ORDER ${value.number} / REVISION ${revision}`,
        value.is_demo ? 'SAMPLE ORDER — NOT A REAL CUSTOMER COMMITMENT' : '',
        `Customer: ${value.customer_name}`,
        `Product: ${value.terms.product_name}`,
        `Quantity: ${value.terms.quantity}`,
        `Variant: ${value.terms.variant}`,
        ...(Object.keys(value.terms.sizes).length
          ? [`Sizes: ${sizesText(value.terms.sizes)}`]
          : []),
        `Pickup: ${dateTime(value.terms.pickup_at, workspace.timezone, true)} (${workspace.timezone})`,
        ...Object.entries(value.terms.specification).map(
          ([key, text]) => `${titleCase(key)}: ${text}`,
        ),
        `Total: ${money(value.terms.total_cents, workspace.currency)}`,
        `Deposit received: ${money(value.deposit_paid_cents, workspace.currency)}`,
        `Balance: ${money(value.balance_cents, workspace.currency)}`,
        '',
        'Reserved resources:',
        ...value.reservations.map((item) => `${item.label}: ${item.quantity} ${item.unit}`),
        '',
        `Downloaded: ${new Date().toISOString()}`,
        'Check the current order in OrderToWork before starting. A downloaded copy does not authorize obsolete work.',
      ];
      const url = URL.createObjectURL(
        new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }),
      );
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${value.number}-R${revision}${value.is_demo ? '-SAMPLE' : ''}.txt`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setDownloadError(errorMessage(e));
    }
  };
  if (!order.production_ready && order.production_status !== 'started')
    return (
      <div className="readiness-layout">
        <Panel className="panel-padded">
          <div className="empty-icon">
            <Clipboard size={26} />
          </div>
          <h2>The order has not been released.</h2>
          <p className="muted section-small">
            Every condition below must be met before the team starts.
          </p>
          <Readiness order={order} />
          {order.production_blockers.length > 0 && (
            <div className="blocker-list">
              <h3>Outstanding conditions</h3>
              <ul>
                {order.production_blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </div>
          )}
          {workspace.role === 'owner' && (
            <div className="form-actions">
              {order.accepted_revision && (
                <Button onClick={onDeposit}>Record received deposit</Button>
              )}
              <Button onClick={onHold}>{order.hold_reason ? 'Review hold' : 'Add a hold'}</Button>
            </div>
          )}
        </Panel>
      </div>
    );
  const value = ticket.data;
  return (
    <>
      <ErrorNotice
        message={ticket.error || action.error || downloadError}
        retry={ticket.error ? () => void ticket.refresh() : undefined}
      />
      {ticket.loading ? (
        <Loading label="Checking the current production ticket…" />
      ) : (
        value && (
          <article className="work-ticket">
            <header className="ticket-header">
              <div>
                <p className="eyebrow">{workspace.name} · work order</p>
                <h2 className="mono">
                  {value.number} / R
                  {typeof value.revision === 'number' ? value.revision : value.revision.number}
                </h2>
                <p>{value.customer_name}</p>
              </div>
              <div>
                <Badge tone="green">
                  {value.production_status === 'started' ? 'In production' : 'Ready for production'}
                </Badge>
                {value.is_demo && <Badge tone="amber">Sample order</Badge>}
              </div>
            </header>
            <div className="ticket-summary">
              <div>
                <label>Quantity & product</label>
                <strong>
                  {value.terms.quantity} {value.terms.product_name.toLowerCase()}
                </strong>
                <span>
                  {titleCase(value.terms.variant)}
                  {Object.keys(value.terms.sizes).length
                    ? ` · ${sizesText(value.terms.sizes)}`
                    : ''}
                </span>
              </div>
              <div>
                <label>Pickup commitment</label>
                <strong>{dateTime(value.terms.pickup_at, workspace.timezone, true)}</strong>
                <span>{workspace.timezone.replaceAll('_', ' ')}</span>
              </div>
              <div>
                <label>Accepted total</label>
                <strong>{money(value.terms.total_cents, workspace.currency)}</strong>
              </div>
              <div>
                <label>Deposit & balance</label>
                <strong>{money(value.deposit_paid_cents, workspace.currency)} recorded</strong>
                <span>{money(value.balance_cents, workspace.currency)} remaining</span>
              </div>
            </div>
            <div className="ticket-body">
              <h3>Approved production specification</h3>
              <Specification terms={value.terms} open />
              <h3 className="section-gap">Reserved resources</h3>
              <div className="ticket-resources">
                {value.reservations.map((item) => (
                  <div key={item.resource_id}>
                    <Check size={16} />
                    <span>{item.label}</span>
                    <strong>
                      {item.quantity} {item.unit}
                    </strong>
                  </div>
                ))}
              </div>
              <p className="ticket-check-note">
                Work from this accepted revision. If the customer asks for another change, stop and
                resolve it with the owner.
              </p>
            </div>
            <footer className="ticket-footer">
              <span>
                <ShieldCheck size={16} /> Customer agreement, resources and required deposit
                checked.
              </span>
              <div>
                <Button onClick={download}>
                  <Download size={16} /> Download ticket
                </Button>
                {value.production_status !== 'started' && (
                  <Button
                    variant="primary"
                    onClick={() =>
                      setStartConfirm(
                        typeof value.revision === 'number' ? value.revision : value.revision.number,
                      )
                    }
                  >
                    Start work <ArrowRight size={16} />
                  </Button>
                )}
              </div>
            </footer>
          </article>
        )
      )}
      {startConfirm && (
        <Modal title="Start this work order?" onClose={() => setStartConfirm(null)}>
          <div className="modal-body">
            <Notice title="The server will check the current revision again.">
              Starting work marks production as underway. Later changes will require an owner’s
              decision.
            </Notice>
            <ErrorNotice message={action.error} />
            <div className="form-actions">
              <Button onClick={() => setStartConfirm(null)}>Cancel</Button>
              <Button
                variant="primary"
                busy={action.pending}
                onClick={() =>
                  void action.run(
                    () =>
                      post<OrderDetail>(`${path}/production/start`, {
                        expected_revision: startConfirm,
                      }),
                    async (next) => {
                      await onUpdate(next);
                      await ticket.refresh();
                      setStartConfirm(null);
                    },
                  )
                }
              >
                Confirm & start work
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

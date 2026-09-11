import { ArrowRight, Check, ClipboardCheck, Filter, Inbox, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from '../lib/workspace';
import { useAuth } from '../lib/auth';
import { OrderBadge, ProductMark, statusLabels } from '../components/orders';
import { Badge, EmptyState, ErrorNotice, Loading, PageIntro, Panel } from '../components/ui';
import { dateTime, money } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { OrderSummary } from '../lib/types';

function orderDescription(order: OrderSummary) {
  if (order.hold_reason) return order.hold_reason;
  if (order.status === 'needs_review') return 'A request or proposal is ready for your review.';
  if (order.status === 'new') return 'Prepare a clear proposal for this customer.';
  if (order.status === 'awaiting_approval')
    return 'The customer has a proposal to review. The current agreement stays protected.';
  if (order.status === 'deposit_due')
    return 'The accepted revision is reserved. Record the required deposit before work starts.';
  if (order.status === 'in_production') return 'Your team has started the accepted work order.';
  if (order.status === 'ready_for_handover')
    return order.handover_status === 'delivery_requested'
      ? 'Review the customer’s delivery address and prepare the exact fee for approval.'
      : order.handover_status === 'quote_ready'
        ? 'The delivery quote is waiting for the customer’s approval.'
        : order.handover_status === 'awaiting_choice'
          ? 'The items are ready. The customer can choose collection or request delivery.'
          : 'The items are finished. Prepare collection details and delivery options.';
  if (order.status === 'awaiting_collection')
    return 'The customer confirmed collection. Record the received balance before handing over the items.';
  if (order.status === 'awaiting_dispatch')
    return 'The customer accepted the delivery quote. Record the received balance and arrange dispatch.';
  if (order.status === 'out_for_delivery')
    return 'The order has been dispatched. Mark it delivered when the customer receives it.';
  if (order.status === 'completed')
    return 'The accepted order, payment and final handover are recorded.';
  return 'The agreement, resource reservations, and required deposit are recorded.';
}

export function DecisionsPage() {
  const { session } = useAuth();
  const workspace = useWorkspace();
  const { data, loading, error, refresh } = useApi<{ orders: OrderSummary[] }>(
    `/workspaces/${workspace.id}/orders`,
    { pollMs: 10000 },
  );
  const orders = data?.orders ?? [];
  const decisions = orders.filter(
    (order) =>
      [
        'needs_review',
        'new',
        'on_hold',
        'awaiting_collection',
        'awaiting_dispatch',
        'out_for_delivery',
      ].includes(order.status) ||
      (order.status === 'ready_for_handover' &&
        (!order.handover_status || order.handover_status === 'delivery_requested')),
  );
  const waiting = orders.filter((order) => !decisions.includes(order));
  const owner = workspace.role === 'owner';
  return (
    <>
      <PageIntro
        eyebrow={new Intl.DateTimeFormat('en-US', {
          weekday: 'long',
          day: 'numeric',
          month: 'long',
          timeZone: workspace.timezone,
        }).format(new Date())}
        title="Your attention, where it matters."
        actions={
          <span className="intro-business">
            {workspace.name}
            <br />
            <span>Made-to-order workspace</span>
          </span>
        }
      >
        <p>A clear view of the decisions that need you, and the orders moving forward.</p>
      </PageIntro>
      <ErrorNotice message={error} retry={() => void refresh()} />
      {loading ? (
        <Loading label="Checking your orders…" />
      ) : (
        <>
          <div className="section-heading">
            <h2>{owner ? 'Needs your decision' : 'Needs attention'}</h2>
            <Badge tone={decisions.length ? 'amber' : 'neutral'}>
              {decisions.length} {decisions.length === 1 ? 'order' : 'orders'}
            </Badge>
          </div>
          {decisions.length ? (
            <div className="decision-stack">
              {decisions.map((order) => (
                <Panel key={order.id} className="decision-card">
                  <div className="decision-card-main">
                    <ProductMark profile={workspace.profile} />
                    <div className="decision-card-copy">
                      <div className="order-eyebrow">
                        <span className="mono">{order.number}</span>
                        <span>·</span>
                        <span>{order.customer_name}</span>
                        {order.is_demo && <Badge tone="amber">Sample</Badge>}
                      </div>
                      <div className="decision-title">
                        <h2>
                          {order.hold_reason
                            ? 'A hold needs a decision.'
                            : order.production_status === 'finished'
                              ? order.handover_status === 'delivery_requested'
                                ? 'A delivery address needs your review.'
                                : order.status === 'out_for_delivery'
                                  ? 'Follow through on the final delivery.'
                                  : order.status === 'awaiting_collection'
                                    ? 'The customer is coming to collect.'
                                    : order.status === 'awaiting_dispatch'
                                      ? 'Delivery is agreed. Arrange dispatch.'
                                      : 'The work is finished. Prepare handover.'
                              : order.accepted_revision
                                ? 'A customer request needs a closer look.'
                                : 'A new order starts here.'}
                        </h2>
                        <OrderBadge status={order.status} />
                      </div>
                      <p>{orderDescription(order)}</p>
                    </div>
                    <Link
                      className="button button-primary"
                      to={`/w/${workspace.id}/orders/${order.id}${order.production_status === 'finished' ? '?tab=handover' : ''}`}
                    >
                      {order.production_status === 'finished' ? 'Review handover' : 'Review order'}{' '}
                      <ArrowRight size={17} />
                    </Link>
                  </div>
                  <div className="decision-card-footer">
                    <span className="status-dot" />
                    {order.accepted_revision
                      ? `Revision ${order.accepted_revision.number} remains the accepted order.`
                      : 'Customer consent is required before confirming the order.'}
                    <span>
                      {order.production_status === 'finished'
                        ? 'A clear choice before the final handover.'
                        : 'A request is distinct from an approval.'}
                    </span>
                  </div>
                </Panel>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Inbox size={30} strokeWidth={1.3} />}
              title={orders.length ? 'No decisions waiting.' : 'A clear start for your next order.'}
              action={
                owner && session?.auth_method !== 'demo' ? (
                  <Link className="button button-primary" to={`/w/${workspace.id}/orders/new`}>
                    <Plus size={16} /> Create an order
                  </Link>
                ) : undefined
              }
            >
              {orders.length
                ? 'Your other orders are listed below. New requests and exceptions will appear here.'
                : 'Add a customer’s order details, then use their messages to check changes and prepare the next step.'}
            </EmptyState>
          )}
          <div className="dashboard-lower">
            <section>
              <div className="quiet-heading">
                <h2>Following through</h2>
                <Link to={`/w/${workspace.id}/orders`}>
                  View all orders <ArrowRight size={14} />
                </Link>
              </div>
              {waiting.length ? (
                waiting.map((order) => (
                  <Link
                    key={order.id}
                    to={`/w/${workspace.id}/orders/${order.id}${order.production_status === 'finished' ? '?tab=handover' : ''}`}
                    className="quiet-order"
                  >
                    <span className="quiet-order-icon">
                      {order.status === 'ready' ? (
                        <Check size={17} />
                      ) : (
                        <ClipboardCheck size={17} />
                      )}
                    </span>
                    <span>
                      <strong>{order.customer_name}</strong>
                      <small>
                        {order.number}
                        {order.accepted_revision
                          ? ` · Revision ${order.accepted_revision.number}`
                          : ''}
                      </small>
                    </span>
                    <OrderBadge status={order.status} />
                  </Link>
                ))
              ) : (
                <p className="quiet-empty">
                  Orders waiting for customers, deposits, or production will appear here.
                </p>
              )}
            </section>
            <section>
              <div className="quiet-heading">
                <h2>Before work begins</h2>
                <span>Every order</span>
              </div>
              <div className="principle-row">
                <span>01</span>
                <div>
                  <strong>An agreed specification</strong>
                  <p>The customer approves the exact quantity, price, and pickup.</p>
                </div>
              </div>
              <div className="principle-row">
                <span>02</span>
                <div>
                  <strong>A promise the shop can keep</strong>
                  <p>Resources are checked again before a revision is confirmed.</p>
                </div>
              </div>
              <div className="principle-row">
                <span>03</span>
                <div>
                  <strong>A clear handoff</strong>
                  <p>Production waits for the deposit and any active hold.</p>
                </div>
              </div>
            </section>
          </div>
        </>
      )}
    </>
  );
}

export function OrdersPage({ production = false }: { production?: boolean }) {
  const { session } = useAuth();
  const workspace = useWorkspace();
  const { data, loading, error, refresh } = useApi<{ orders: OrderSummary[] }>(
    `/workspaces/${workspace.id}/orders`,
    { pollMs: 10000 },
  );
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const orders = (data?.orders ?? []).filter(
    (order) =>
      `${order.customer_name} ${order.number}`.toLowerCase().includes(query.toLowerCase()) &&
      (status === 'all' || order.status === status),
  );
  const ready = orders.filter((order) =>
    [
      'ready',
      'in_production',
      'ready_for_handover',
      'awaiting_collection',
      'awaiting_dispatch',
      'out_for_delivery',
    ].includes(order.status),
  );
  const shown = production ? ready : orders;
  return (
    <>
      <PageIntro
        eyebrow={production ? 'Production handoff' : 'Order register'}
        title={
          production
            ? 'Agreed. Checked. Ready for the team.'
            : 'Every order has a clear commitment.'
        }
        actions={
          workspace.role === 'owner' && !production && session?.auth_method !== 'demo' ? (
            <Link className="button button-primary" to={`/w/${workspace.id}/orders/new`}>
              <Plus size={17} /> New order
            </Link>
          ) : undefined
        }
      >
        <p>
          {production
            ? 'Only orders with an accepted revision, reserved resources, the required deposit, and no hold appear as ready.'
            : 'Pending proposals stay separate from what the customer has already accepted.'}
        </p>
      </PageIntro>
      <ErrorNotice message={error} retry={() => void refresh()} />
      <div className="list-toolbar">
        <div className="search-field">
          <Search size={17} />
          <label className="sr-only" htmlFor="order-search">
            Search orders
          </label>
          <input
            id="order-search"
            placeholder="Search customer or order…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {!production && (
          <div className="filter-field">
            <Filter size={16} />
            <label className="sr-only" htmlFor="order-status">
              Filter by status
            </label>
            <select id="order-status" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">All statuses</option>
              {Object.entries(statusLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      {loading ? (
        <Loading label="Loading orders…" />
      ) : shown.length ? (
        <Panel className="order-list">
          {shown.map((order) => (
            <Link
              className="order-list-row"
              to={`/w/${workspace.id}/orders/${order.id}${production ? '?tab=production' : ''}`}
              key={order.id}
            >
              <ProductMark profile={workspace.profile} small />
              <div className="order-list-primary">
                <div>
                  <strong>{order.customer_name}</strong>
                  {order.is_demo && <Badge tone="amber">Sample</Badge>}
                </div>
                <span className="mono">{order.number}</span>
              </div>
              <div className="order-list-spec">
                {order.accepted_revision ? (
                  <>
                    <strong>
                      {order.accepted_revision.terms.quantity}{' '}
                      {order.accepted_revision.terms.product_name.toLowerCase()}
                    </strong>
                    <span>
                      Revision {order.accepted_revision.number} ·{' '}
                      {dateTime(order.accepted_revision.terms.pickup_at, workspace.timezone)}
                    </span>
                  </>
                ) : (
                  <>
                    <strong>No accepted revision yet</strong>
                    <span>Customer review required</span>
                  </>
                )}
              </div>
              <div className="order-list-price">
                {order.accepted_revision
                  ? money(order.accepted_revision.terms.total_cents, workspace.currency)
                  : '—'}
              </div>
              <OrderBadge status={order.status} />
              <ArrowRight size={17} className="row-arrow" />
            </Link>
          ))}
        </Panel>
      ) : (
        <EmptyState
          title={
            production
              ? 'No released work yet.'
              : query || status !== 'all'
                ? 'No orders match this view.'
                : 'Your first order belongs here.'
          }
          action={
            production ? (
              <Link to={`/w/${workspace.id}/orders`} className="button button-secondary">
                View outstanding conditions <ArrowRight size={16} />
              </Link>
            ) : workspace.role === 'owner' && session?.auth_method !== 'demo' ? (
              <Link className="button button-primary" to={`/w/${workspace.id}/orders/new`}>
                <Plus size={16} /> Create an order
              </Link>
            ) : undefined
          }
        >
          {production
            ? 'An order appears here once all production conditions are met.'
            : 'Start with the product and customer details. Prices will be calculated from the saved rate card.'}
        </EmptyState>
      )}
      {production && Boolean((data?.orders.length ?? 0) - ready.length) && (
        <p className="footnote">
          {(data?.orders.length ?? 0) - ready.length} other orders have not been released.{' '}
          <Link to={`/w/${workspace.id}/orders`}>Review their conditions.</Link>
        </p>
      )}
    </>
  );
}

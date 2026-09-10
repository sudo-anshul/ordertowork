import { ArrowLeft, ArrowRight, Check, Download, PackageCheck, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useWorkspace } from '../lib/workspace';
import { ProductMark, Specification, sizesText } from '../components/orders';
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Loading,
  Modal,
  Notice,
  PageIntro,
  Panel,
} from '../components/ui';
import { post } from '../lib/api';
import { dateTime, titleCase } from '../lib/format';
import { useAction, useApi } from '../lib/hooks';
import type { OperationalTicket, ProductionOrder } from '../lib/types';

export function ProductionQueuePage() {
  const workspace = useWorkspace();
  const { data, loading, error, refresh } = useApi<{ orders: ProductionOrder[] }>(
    `/workspaces/${workspace.id}/production`,
    { pollMs: 10000 },
  );
  return (
    <>
      <PageIntro eyebrow="Production handoff" title="Agreed. Checked. Ready for the team.">
        <p>
          Released work and orders already in production. Open the current ticket before starting.
        </p>
      </PageIntro>
      <ErrorNotice message={error} retry={() => void refresh()} />
      {loading ? (
        <Loading label="Loading released work…" />
      ) : data?.orders.length ? (
        <Panel className="order-list production-list">
          {data.orders.map((order) => (
            <Link
              className="order-list-row"
              to={`/w/${workspace.id}/production/${order.id}`}
              key={order.id}
            >
              <ProductMark profile={workspace.profile} small />
              <div className="order-list-primary">
                <div>
                  <strong>{order.customer_name}</strong>
                  {order.is_demo && <Badge tone="amber">Sample</Badge>}
                </div>
                <span className="mono">
                  {order.number} · R{order.revision}
                </span>
              </div>
              <div className="order-list-spec">
                <strong>
                  {order.quantity} {order.product_name.toLowerCase()} · {titleCase(order.variant)}
                </strong>
                <span>{dateTime(order.pickup_at, workspace.timezone)}</span>
              </div>
              <Badge tone="green">
                {order.production_status === 'started' ? 'In production' : 'Ready for work'}
              </Badge>
              <ArrowRight size={17} className="row-arrow" />
            </Link>
          ))}
        </Panel>
      ) : (
        <EmptyState
          icon={<PackageCheck size={29} strokeWidth={1.4} />}
          title="No released work yet."
          action={
            workspace.role === 'owner' ? (
              <Link className="button button-secondary" to={`/w/${workspace.id}/orders`}>
                Review outstanding conditions <ArrowRight size={16} />
              </Link>
            ) : undefined
          }
        >
          An order appears here once the agreement, resources, and required deposit are recorded and
          any production hold is resolved.
        </EmptyState>
      )}
    </>
  );
}

export function ProductionTicketPage() {
  const workspace = useWorkspace();
  const { orderId } = useParams();
  const path = `/workspaces/${workspace.id}/orders/${orderId}`;
  const {
    data: ticket,
    loading,
    error,
    refresh,
  } = useApi<OperationalTicket>(`${path}/ticket`, { pollMs: 10000 });
  const [confirm, setConfirm] = useState<number | null>(null);
  const action = useAction();
  const revision = ticket
    ? typeof ticket.revision === 'number'
      ? ticket.revision
      : ticket.revision.number
    : 0;
  const download = () => {
    if (!ticket) return;
    const lines = [
      workspace.name,
      `WORK ORDER ${ticket.number} / REVISION ${revision}`,
      ticket.is_demo ? 'SAMPLE ORDER' : '',
      `Customer: ${ticket.customer_name}`,
      `${ticket.terms.quantity} ${ticket.terms.product_name} · ${ticket.terms.variant}`,
      sizesText(ticket.terms.sizes),
      `Pickup: ${dateTime(ticket.terms.pickup_at, workspace.timezone, true)} (${workspace.timezone})`,
      ...Object.entries(ticket.terms.specification).map(
        ([key, value]) => `${titleCase(key)}: ${value}`,
      ),
      '',
      'Reserved resources:',
      ...ticket.reservations.map((item) => `${item.label}: ${item.quantity} ${item.unit}`),
      '',
      `Downloaded ${new Date().toISOString()}`,
      'Check the current order online before starting work. A downloaded copy does not authorize obsolete production.',
    ];
    const url = URL.createObjectURL(
      new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${ticket.number}-R${revision}-work-ticket.txt`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <>
      <PageIntro
        eyebrow="Current production ticket"
        title="One version. Ready for the team."
        actions={
          <Link className="button button-secondary" to={`/w/${workspace.id}/production`}>
            <ArrowLeft size={16} /> Back to production
          </Link>
        }
      >
        <p>
          Work from the current agreed specification. Starting work checks the release conditions
          again.
        </p>
      </PageIntro>
      <ErrorNotice
        message={error || action.error}
        retry={error ? () => void refresh() : undefined}
      />
      {loading ? (
        <Loading label="Checking this work order…" />
      ) : (
        ticket && (
          <article className="work-ticket">
            <header className="ticket-header">
              <div>
                <p className="eyebrow">{workspace.name} · work order</p>
                <h2 className="mono">
                  {ticket.number} / R{revision}
                </h2>
                <p>{ticket.customer_name}</p>
              </div>
              <div>
                <Badge tone="green">
                  {ticket.production_status === 'started'
                    ? 'In production'
                    : 'Ready for production'}
                </Badge>
                {ticket.is_demo && <Badge tone="amber">Sample order</Badge>}
              </div>
            </header>
            <div className="ticket-summary">
              <div>
                <label>Quantity & specification</label>
                <strong>
                  {ticket.terms.quantity} {ticket.terms.product_name.toLowerCase()}
                </strong>
                <span>
                  {titleCase(ticket.terms.variant)}
                  {Object.keys(ticket.terms.sizes).length
                    ? ` · ${sizesText(ticket.terms.sizes)}`
                    : ''}
                </span>
              </div>
              <div>
                <label>Pickup commitment</label>
                <strong>{dateTime(ticket.terms.pickup_at, workspace.timezone, true)}</strong>
                <span>{workspace.timezone.replaceAll('_', ' ')}</span>
              </div>
            </div>
            <div className="ticket-body">
              <h3>Approved production specification</h3>
              <Specification terms={ticket.terms} open />
              <h3 className="section-gap">Reserved resources</h3>
              <div className="ticket-resources">
                {ticket.reservations.map((item) => (
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
                If the customer requests another change, involve the workspace owner before
                continuing.
              </p>
            </div>
            <footer className="ticket-footer">
              <span>
                <ShieldCheck size={16} /> Current agreement and production release conditions
                checked.
              </span>
              <div>
                <Button onClick={download}>
                  <Download size={16} /> Download ticket
                </Button>
                {ticket.production_status !== 'started' && (
                  <Button variant="primary" onClick={() => setConfirm(revision)}>
                    Start work <ArrowRight size={16} />
                  </Button>
                )}
              </div>
            </footer>
          </article>
        )
      )}
      {confirm && (
        <Modal title="Start this work order?" onClose={() => setConfirm(null)}>
          <div className="modal-body">
            <Notice title="The current revision will be checked again.">
              This records that the team has started production. Later changes require an owner’s
              decision.
            </Notice>
            <ErrorNotice message={action.error} />
            <div className="form-actions">
              <Button onClick={() => setConfirm(null)}>Cancel</Button>
              <Button
                variant="primary"
                busy={action.pending}
                onClick={() =>
                  void action.run(
                    () => post(`${path}/production/start`, { expected_revision: confirm }),
                    async () => {
                      await refresh();
                      setConfirm(null);
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

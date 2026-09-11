import { CakeSlice, Check, Clock3, Shirt } from 'lucide-react';
import type { OrderSummary, Terms } from '../lib/types';
import { dateTime, money, titleCase } from '../lib/format';
import { Badge } from './ui';

export const statusLabels: Record<string, string> = {
  needs_review: 'Needs a decision',
  awaiting_approval: 'Awaiting customer',
  ready: 'Ready for work',
  deposit_due: 'Deposit needed',
  on_hold: 'On hold',
  in_production: 'In production',
  ready_for_handover: 'Ready for handover',
  awaiting_collection: 'Awaiting collection',
  awaiting_dispatch: 'Awaiting dispatch',
  out_for_delivery: 'Out for delivery',
  completed: 'Completed',
  new: 'New request',
};
export function OrderBadge({ status }: { status: string }) {
  return (
    <Badge
      tone={
        ['ready', 'in_production', 'completed'].includes(status)
          ? 'green'
          : [
                'ready_for_handover',
                'awaiting_collection',
                'awaiting_dispatch',
                'out_for_delivery',
              ].includes(status)
            ? 'blue'
            : ['needs_review', 'on_hold', 'deposit_due', 'new'].includes(status)
              ? 'amber'
              : 'neutral'
      }
    >
      {statusLabels[status] ?? titleCase(status)}
    </Badge>
  );
}

export function ProductMark({ profile, small = false }: { profile?: string; small?: boolean }) {
  return (
    <div className={`product-mark ${small ? 'product-mark-small' : ''}`} aria-hidden="true">
      {profile === 'bakery' ? (
        <CakeSlice size={small ? 24 : 38} strokeWidth={1.1} />
      ) : (
        <Shirt size={small ? 24 : 38} strokeWidth={1.1} />
      )}
    </div>
  );
}

export function sizesText(sizes: Record<string, number> | null | undefined) {
  return Object.entries(sizes ?? {})
    .map(([size, count]) => `${size} ${count}`)
    .join(' · ');
}

export function termsRows(terms: Terms, timezone?: string): [string, string][] {
  return [
    ['Product', terms.product_name],
    ['Quantity', String(terms.quantity)],
    ['Variant', titleCase(terms.variant)],
    ...(Object.keys(terms.sizes ?? {}).length
      ? [['Sizes', sizesText(terms.sizes)] as [string, string]]
      : []),
    ['Pickup', dateTime(terms.pickup_at, timezone, true)],
    ['Order total', money(terms.total_cents, terms.currency)],
  ];
}

export function TermsDiff({
  before,
  after,
  timezone,
  customer = false,
}: {
  before?: Terms | null;
  after: Terms;
  timezone?: string;
  customer?: boolean;
}) {
  const next = termsRows(after, timezone);
  const old = before ? new Map(termsRows(before, timezone)) : null;
  if (customer)
    return (
      <div className="customer-diff">
        {next
          .filter(([label]) => label !== 'Order total')
          .map(([label, value]) => (
            <div className="customer-diff-row" key={label}>
              <div className="customer-diff-label">{label}</div>
              {old && old.get(label) !== value && (
                <p className="customer-before">Previously: {old.get(label) ?? 'Not specified'}</p>
              )}
              <p className="customer-after">
                {value}
                {old && old.get(label) === value && (
                  <span className="unchanged-label">Unchanged</span>
                )}
              </p>
            </div>
          ))}
        <p className="timezone-note">
          Pickup shown in {timezone?.replaceAll('_', ' ') || 'shop local time'}.
        </p>
      </div>
    );
  return (
    <div className="terms-comparison">
      <div className="comparison-header">
        <span>Detail</span>
        {old && <span>Currently confirmed</span>}
        <span>{old ? 'Proposed revision' : 'Proposed order'}</span>
      </div>
      {next.map(([label, value]) => (
        <div
          className={`comparison-row ${old && old.get(label) !== value ? 'changed' : ''} ${!old ? 'no-previous' : ''}`}
          key={label}
        >
          <span className="comparison-label">{label}</span>
          {old && (
            <span className="comparison-before">
              <span className="mobile-column-label">Currently confirmed</span>
              {old.get(label) ?? 'Not specified'}
            </span>
          )}
          <span className="comparison-after">
            <span className="mobile-column-label">Proposed</span>
            {value}
          </span>
        </div>
      ))}
      <p className="timezone-note">
        Pickup shown in {timezone?.replaceAll('_', ' ') || 'shop local time'}.
      </p>
    </div>
  );
}

export function Specification({
  terms,
  open = false,
}: {
  terms: Pick<Terms, 'specification'>;
  open?: boolean;
}) {
  const entries = Object.entries(terms.specification ?? {}).filter(
    ([, value]) => value != null && String(value).trim(),
  );
  if (!entries.length) return null;
  return (
    <details className="specification" open={open || undefined}>
      <summary>Review the full specification</summary>
      <dl>
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{titleCase(key)}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function Readiness({
  order,
}: {
  order: OrderSummary & {
    reservations?: unknown[];
    production_blockers?: string[];
    production_ready?: boolean;
  };
}) {
  const accepted = order.accepted_revision;
  const required = accepted?.terms.required_deposit_cents ?? 0;
  const rows = [
    {
      done: Boolean(accepted),
      label: 'Customer agreement',
      text: accepted ? `Revision ${accepted.number} confirmed` : 'Awaiting exact revision approval',
    },
    {
      done: Boolean(order.reservations?.length) || order.production_status !== 'not_started',
      label: 'Resources',
      text:
        order.production_status !== 'not_started'
          ? 'Committed to the accepted work'
          : order.reservations?.length
            ? 'Reserved for the accepted revision'
            : 'Not yet reserved',
    },
    {
      done: Boolean(accepted && order.deposit_paid_cents >= required),
      label: 'Required deposit',
      text: accepted
        ? order.deposit_paid_cents >= required
          ? `${money(order.deposit_paid_cents, accepted.terms.currency)} recorded`
          : `${money(required - order.deposit_paid_cents, accepted.terms.currency)} still needed`
        : 'Set when terms are accepted',
    },
    {
      done: !order.hold_reason,
      label: 'Production hold',
      text: order.hold_reason || 'No active hold',
    },
  ];
  return (
    <div className="readiness-list">
      {rows.map((row) => (
        <div className="readiness-item" key={row.label}>
          <span className={`readiness-icon ${row.done ? 'done' : ''}`}>
            {row.done ? <Check size={14} /> : <Clock3 size={14} />}
          </span>
          <div>
            <strong>{row.label}</strong>
            <p>{row.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

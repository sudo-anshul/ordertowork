import { Check, MapPin, PackageCheck, Truck } from 'lucide-react';
import { dateTime, money } from '../lib/format';
import { handoverLabel } from '../lib/handover';
import type { Handover, HandoverPricing } from '../lib/types';
import { Badge, KeyValue } from './ui';

export function HandoverBadge({ handover }: { handover: Handover }) {
  return (
    <Badge
      tone={
        ['collected', 'delivered', 'confirmed'].includes(handover.status)
          ? 'green'
          : handover.status === 'delivery_requested'
            ? 'amber'
            : 'blue'
      }
    >
      {handoverLabel(handover)}
    </Badge>
  );
}

export function HandoverProgress({
  handover,
  customer = false,
}: {
  handover: Handover;
  customer?: boolean;
}) {
  const stage = ['collected', 'delivered'].includes(handover.status)
    ? 3
    : ['confirmed', 'out_for_delivery'].includes(handover.status)
      ? 2
      : ['delivery_requested', 'quote_ready'].includes(handover.status)
        ? 1
        : 0;
  const labels = ['Ready', customer ? 'Your choice' : 'Customer choice', 'Confirmed', 'Complete'];
  return (
    <ol className="handover-progress" aria-label="Handover progress">
      {labels.map((label, index) => (
        <li
          key={label}
          className={index <= stage ? 'done' : ''}
          aria-current={index === stage ? 'step' : undefined}
        >
          <span className="handover-step-mark" aria-hidden="true">
            {index < stage || stage === 3 ? <Check size={13} /> : index + 1}
          </span>
          <span>{label}</span>
        </li>
      ))}
    </ol>
  );
}

export function HandoverPrice({
  pricing,
  method,
  quote = false,
}: {
  pricing: HandoverPricing;
  method: Handover['method'];
  quote?: boolean;
}) {
  return (
    <section className="handover-price" aria-label="Order and handover amounts">
      <p className="eyebrow">Every amount, in view</p>
      <dl>
        <KeyValue label="Agreed order total">
          {money(pricing.order_total_cents, pricing.currency)}
        </KeyValue>
        <KeyValue label={method === 'collection' ? 'Collection fee' : 'Delivery fee'}>
          {pricing.delivery_fee_cents === null
            ? method === 'delivery'
              ? 'Awaiting review'
              : 'After your choice'
            : pricing.delivery_fee_cents === 0
              ? 'No extra charge'
              : money(pricing.delivery_fee_cents, pricing.currency)}
        </KeyValue>
        <KeyValue label={quote ? 'Total with this quote' : 'Total including handover'}>
          {pricing.total_cents === null
            ? 'Not final yet'
            : money(pricing.total_cents, pricing.currency)}
        </KeyValue>
        <KeyValue label="Payments recorded">{money(pricing.paid_cents, pricing.currency)}</KeyValue>
      </dl>
      <div className="handover-balance">
        <span>{quote ? 'Balance if accepted' : 'Balance remaining'}</span>
        <strong>
          {pricing.balance_cents === null
            ? 'To be confirmed'
            : money(pricing.balance_cents, pricing.currency)}
        </strong>
      </div>
      <p>Arrange payment directly with the business. This page does not take payment.</p>
    </section>
  );
}

export function HandoverDetails({ handover, timezone }: { handover: Handover; timezone: string }) {
  if (!handover.method) return null;
  const collection = handover.method === 'collection';
  return (
    <section className="handover-destination" aria-label="Handover details">
      <div className="handover-destination-heading">
        {collection ? <MapPin size={21} /> : <Truck size={21} />}
        <h3>{collection ? 'Collection details' : 'Delivery details'}</h3>
      </div>
      <dl>
        <KeyValue label={collection ? 'Collect from' : 'Delivery address'}>
          <span className="preserve-lines">
            {collection ? handover.config.collection_address : handover.delivery_address}
          </span>
        </KeyValue>
        {handover.collection_at && (
          <KeyValue label="Collection time">
            {dateTime(handover.collection_at, timezone, true)}
          </KeyValue>
        )}
        {collection && handover.config.collection_instructions && (
          <KeyValue label="Opening hours & instructions">
            <span className="preserve-lines">{handover.config.collection_instructions}</span>
          </KeyValue>
        )}
        {handover.contact_phone && (
          <KeyValue label="Contact number">{handover.contact_phone}</KeyValue>
        )}
        {handover.customer_note && (
          <KeyValue label="Customer note">
            <span className="preserve-lines">{handover.customer_note}</span>
          </KeyValue>
        )}
      </dl>
      {collection && <p className="timezone-note">Times in {timezone.replaceAll('_', ' ')}.</p>}
      {handover.completed_at && (
        <p className="handover-completed-note">
          <PackageCheck size={16} /> {handoverLabel(handover)}{' '}
          {dateTime(handover.completed_at, timezone)}.
        </p>
      )}
    </section>
  );
}

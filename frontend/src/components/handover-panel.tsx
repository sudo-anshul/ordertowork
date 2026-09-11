import {
  ArrowRight,
  Check,
  Copy,
  ExternalLink,
  Link2,
  MapPin,
  PackageCheck,
  Truck,
} from 'lucide-react';
import { DateTime } from 'luxon';
import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { post, newIdempotencyKey } from '../lib/api';
import { dateTime, money } from '../lib/format';
import { centsFromInput, collectionISO, deliveryDescription, handoverError } from '../lib/handover';
import { useAction, useApi } from '../lib/hooks';
import type {
  DeliveryMode,
  Handover,
  HandoverDefaults,
  HandoverResponse,
  HandoverShare,
  OrderDetail,
  Workspace,
} from '../lib/types';
import { useWorkspace } from '../lib/workspace';
import { HandoverWindowModal } from './handover-window';
import {
  HandoverBadge,
  HandoverDetails,
  HandoverPrice,
  HandoverProgress,
} from './handover-summary';
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Notice,
  Panel,
  PanelHeading,
} from './ui';

export function HandoverPanel({
  order,
  path,
  onUpdated,
  onHold,
}: {
  order: OrderDetail;
  path: string;
  onUpdated: () => Promise<void>;
  onHold: () => void;
}) {
  const workspace = useWorkspace();
  const endpoint = `${path}/handover`;
  const { data, error, loading, refresh, setData } = useApi<HandoverResponse>(endpoint, {
    pollMs: 5000,
  });
  const [shareOpen, setShareOpen] = useState(false);
  const [quote, setQuote] = useState<Handover | null>(null);
  const [receipt, setReceipt] = useState<Handover | null>(null);
  const [windowEdit, setWindowEdit] = useState<Handover | null>(null);
  const [transition, setTransition] = useState<{
    action: 'dispatch' | 'collect' | 'deliver';
    handover: Handover;
  } | null>(null);
  const action = useAction();
  const save = async (next: HandoverResponse) => {
    setData(next);
    await onUpdated();
  };
  const reload = () => {
    setQuote(null);
    setReceipt(null);
    setWindowEdit(null);
    setTransition(null);
    action.clearError();
    void refresh();
  };
  const handover = data?.handover;
  const handoverTimezone = handover?.config.timezone ?? workspace.timezone;
  if (loading) return <Loading label="Checking the next step after production…" />;
  if (!data)
    return (
      <ErrorNotice
        message={error || 'Handover details could not be loaded.'}
        retry={() => void refresh()}
      />
    );
  if (order.production_status === 'not_started')
    return (
      <Panel>
        <EmptyState
          icon={<PackageCheck size={30} strokeWidth={1.4} />}
          title="A clear finish, after the work is done."
          action={
            <Link className="button button-secondary" to={`?tab=production`}>
              View production <ArrowRight size={16} />
            </Link>
          }
        >
          Start production from the accepted work ticket. Once the items are finished, prepare
          collection and delivery options here.
        </EmptyState>
      </Panel>
    );
  if (!handover)
    return (
      <div className="handover-layout">
        <Panel className="panel-padded">
          <PanelHeading title="Ready for handover">
            <Badge tone="green">Revision {order.accepted_revision?.number}</Badge>
          </PanelHeading>
          <p className="handover-intro">
            When the work is finished, make the final step just as clear as the first agreement.
          </p>
          <ErrorNotice message={error} retry={() => void refresh()} />
          {order.hold_reason && (
            <Notice tone="warning" title="Resolve the active hold first.">
              <p>{order.hold_reason}</p>
              <Button variant="ghost" onClick={onHold}>
                Review hold <ArrowRight size={14} />
              </Button>
            </Notice>
          )}
          <ReadyForm
            order={order}
            workspace={workspace}
            defaults={data.defaults}
            endpoint={endpoint}
            onSaved={save}
          />
        </Panel>
        <aside className="handover-aside">
          <Panel className="panel-padded handover-guide">
            <span className="handover-guide-icon">
              <PackageCheck size={24} strokeWidth={1.4} />
            </span>
            <p className="eyebrow">Finish with a clear agreement</p>
            <h2>Made. Agreed. Handed over.</h2>
            <ol>
              <li>
                <strong>Prepare the options</strong>
                <span>
                  Confirm the items are finished and save collection details and delivery rules.
                </span>
              </li>
              <li>
                <strong>Share the customer link</strong>
                <span>
                  Copy a prepared message into your usual channel. The customer chooses collection
                  or requests delivery.
                </span>
              </li>
              <li>
                <strong>Close the order</strong>
                <span>
                  Confirm any delivery fee, record the received balance, then record collection or
                  delivery.
                </span>
              </li>
            </ol>
          </Panel>
          <p className="aside-note">
            Delivery fees come from your saved rules. You check each delivery address before making
            a promise.
          </p>
        </aside>
      </div>
    );
  const complete = ['collected', 'delivered'].includes(handover.status);
  const balanceDue = (handover.pricing.balance_cents ?? 0) > 0;
  const canHandover =
    handover.status === 'confirmed' && handover.pricing.balance_cents === 0 && !handover.on_hold;
  return (
    <>
      <ErrorNotice message={error || action.error} retry={reload} />
      <div className="handover-layout">
        <div className="handover-main">
          <Panel className="panel-padded">
            <PanelHeading
              title={
                complete ? 'The order is complete.' : 'From finished work to a happy handover.'
              }
            >
              <div className="action-cluster">
                <HandoverBadge handover={handover} />
                {!complete && !handover.on_hold && (
                  <Button variant="ghost" onClick={onHold}>
                    Pause handover
                  </Button>
                )}
              </div>
            </PanelHeading>
            <HandoverProgress handover={handover} />
            {handover.on_hold && (
              <Notice tone="warning" title="Handover is on hold.">
                <p>
                  {order.hold_reason || 'Resolve the active hold before moving this order forward.'}
                </p>
                <Button variant="ghost" onClick={onHold}>
                  Review hold <ArrowRight size={14} />
                </Button>
              </Notice>
            )}
            {handover.status === 'awaiting_choice' && (
              <div className="handover-next">
                <span className="handover-next-icon">
                  <MapPin size={23} />
                </span>
                <div>
                  <h3>The customer has the next choice.</h3>
                  <p>
                    Create a private link and share the prepared message. They can confirm
                    collection or request delivery.
                  </p>
                </div>
              </div>
            )}
            {handover.status === 'delivery_requested' && (
              <Notice tone="warning" title="Check this address before offering delivery.">
                The customer has requested delivery. Review their address against your service area,
                then prepare the exact fee for their approval. Delivery is not confirmed yet.
              </Notice>
            )}
            {handover.status === 'quote_ready' && (
              <Notice title="The delivery quote is waiting for the customer.">
                The customer must accept this exact delivery fee and total before you dispatch.
                Their existing link shows the updated quote.
              </Notice>
            )}
            {handover.status === 'confirmed' && (
              <Notice
                tone="success"
                title={
                  handover.method === 'collection'
                    ? 'The customer has confirmed collection.'
                    : 'The customer accepted the delivery quote.'
                }
              >
                {balanceDue
                  ? 'Record the remaining balance once you receive it, then complete the handover.'
                  : 'The full balance is recorded. Confirm the physical handover when it happens.'}
              </Notice>
            )}
            {handover.status === 'out_for_delivery' && (
              <Notice tone="success" title="The order is out for delivery.">
                Record delivery after the customer receives the items. Their private link already
                shows this status.
              </Notice>
            )}
            {complete && (
              <Notice
                tone="success"
                title={
                  handover.status === 'collected' ? 'Collection recorded.' : 'Delivery recorded.'
                }
              >
                The agreed order and handover are complete. The timeline preserves the customer’s
                choice, payments and final handover.
              </Notice>
            )}
            <HandoverDetails handover={handover} timezone={handoverTimezone} />
            {handover.quote_note && (
              <div className="handover-quote-note">
                <strong>Your delivery note</strong>
                <p>{handover.quote_note}</p>
              </div>
            )}
            {['delivery_requested', 'quote_ready'].includes(handover.status) && (
              <div className="form-actions">
                <Button
                  variant="primary"
                  disabled={handover.on_hold}
                  onClick={() => setQuote(handover)}
                >
                  <Truck size={16} />{' '}
                  {handover.status === 'quote_ready'
                    ? 'Revise delivery quote'
                    : 'Review delivery & prepare quote'}
                </Button>
              </div>
            )}
            {handover.status === 'confirmed' && (
              <div className="handover-action-block">
                {balanceDue && (
                  <Button
                    variant="primary"
                    disabled={handover.on_hold}
                    onClick={() => setReceipt(handover)}
                  >
                    Record balance received <ArrowRight size={16} />
                  </Button>
                )}
                <Button
                  variant={balanceDue ? 'secondary' : 'primary'}
                  disabled={!canHandover}
                  onClick={() =>
                    setTransition({
                      action: handover.method === 'collection' ? 'collect' : 'dispatch',
                      handover,
                    })
                  }
                >
                  {handover.method === 'collection' ? (
                    <PackageCheck size={16} />
                  ) : (
                    <Truck size={16} />
                  )}
                  {handover.method === 'collection' ? 'Mark collected' : 'Mark out for delivery'}
                </Button>
                {balanceDue && (
                  <p className="field-hint">
                    Record the full outstanding balance before collection or dispatch.
                  </p>
                )}
              </div>
            )}
            {handover.status === 'out_for_delivery' && (
              <div className="form-actions">
                <Button
                  variant="primary"
                  disabled={handover.on_hold}
                  onClick={() => setTransition({ action: 'deliver', handover })}
                >
                  <PackageCheck size={16} /> Mark delivered
                </Button>
              </div>
            )}
          </Panel>
          <Panel className="panel-padded handover-share-panel">
            <div>
              <span className="eyebrow">Customer communication</span>
              <h2>A private link. A message ready to share.</h2>
              <p>
                {handover.link_active
                  ? 'The customer’s existing link reflects the latest status. Create a replacement only if you need a new link and message; the old link will stop working.'
                  : 'Generate a private customer link and a message for this stage. Copy them into your usual messaging channel.'}
              </p>
            </div>
            <Button onClick={() => setShareOpen(true)} disabled={action.pending}>
              <Link2 size={16} />{' '}
              {handover.link_active ? 'Create replacement link' : 'Create customer link'}
            </Button>
            <p className="field-hint">
              OrderToWork prepares the message. No email or message is sent automatically.
            </p>
          </Panel>
        </div>
        <aside className="handover-aside">
          <HandoverPrice
            pricing={handover.pricing}
            method={handover.method}
            quote={handover.status === 'quote_ready'}
          />
          <Panel className="panel-padded handover-rules">
            <h3>Options saved for this order</h3>
            <p className="preserve-lines">
              <strong>Collection</strong>
              {handover.config.collection_address}
            </p>
            {handover.config.collection_instructions && (
              <p className="preserve-lines">{handover.config.collection_instructions}</p>
            )}
            <p>
              <strong>Collection window</strong>
              {dateTime(handover.config.collection_window_start, handoverTimezone)} –{' '}
              {dateTime(handover.config.collection_window_end, handoverTimezone)}
            </p>
            <p>
              <strong>Delivery</strong>
              {deliveryDescription(handover.config, handover.pricing.currency)}
            </p>
            {handover.config.delivery_area && (
              <p>
                <strong>Service area</strong>
                {handover.config.delivery_area}
              </p>
            )}
            <p className="field-hint">
              Times in {handoverTimezone.replaceAll('_', ' ')}. Address and delivery rules are fixed
              for revision {handover.revision}.
            </p>
            {!complete && handover.status !== 'out_for_delivery' && (
              <Button
                className="section-small"
                disabled={handover.on_hold}
                onClick={() => setWindowEdit(handover)}
              >
                Update collection window
              </Button>
            )}
          </Panel>
          {order.is_demo && (
            <p className="aside-note">
              Sample order. Use fictional addresses and sample receipt references.
            </p>
          )}
        </aside>
      </div>
      {shareOpen && (
        <HandoverShareModal
          endpoint={endpoint}
          handover={handover}
          timezone={handoverTimezone}
          onClose={() => setShareOpen(false)}
          onShared={() => refresh(true)}
        />
      )}
      {quote && (
        <QuoteModal
          endpoint={endpoint}
          initial={quote}
          latest={handover}
          timezone={handoverTimezone}
          onClose={() => setQuote(null)}
          onReload={reload}
          onSaved={async (next) => {
            await save(next);
            setQuote(null);
          }}
        />
      )}
      {receipt && (
        <ReceiptModal
          endpoint={endpoint}
          handover={receipt}
          sample={order.is_demo}
          onClose={() => setReceipt(null)}
          onSaved={async (next) => {
            await save(next);
            setReceipt(null);
          }}
        />
      )}
      {windowEdit && (
        <HandoverWindowModal
          endpoint={endpoint}
          initial={windowEdit}
          latest={handover}
          timezone={handoverTimezone}
          onClose={() => setWindowEdit(null)}
          onReload={reload}
          onSaved={async (next) => {
            await save(next);
            setWindowEdit(null);
          }}
        />
      )}
      {transition && (
        <Modal
          title={
            transition.action === 'dispatch'
              ? 'Has this order left for delivery?'
              : transition.action === 'collect'
                ? 'Has the customer collected this order?'
                : 'Has the customer received this order?'
          }
          onClose={() => setTransition(null)}
        >
          <div className="modal-body form-stack">
            <p className="muted">
              This records the physical{' '}
              {transition.action === 'dispatch'
                ? 'dispatch'
                : transition.action === 'collect'
                  ? 'collection'
                  : 'delivery'}{' '}
              of {order.number}. The customer’s page will show the updated status.
            </p>
            {transition.handover.version !== handover.version && (
              <Notice tone="warning" title="This handover changed.">
                Reload the latest details before confirming.
                <Button variant="ghost" onClick={reload}>
                  Reload handover
                </Button>
              </Notice>
            )}
            <ErrorNotice message={action.error} retry={reload} />
            <div className="form-actions">
              <Button onClick={() => setTransition(null)} disabled={action.pending}>
                Cancel
              </Button>
              <Button
                variant="primary"
                busy={action.pending}
                disabled={transition.handover.version !== handover.version || handover.on_hold}
                onClick={() =>
                  void action.run(
                    async () => {
                      try {
                        return await post<HandoverResponse>(`${endpoint}/${transition.action}`, {
                          expected_version: transition.handover.version,
                        });
                      } catch (e) {
                        throw new Error(handoverError(e));
                      }
                    },
                    async (next) => {
                      await save(next);
                      setTransition(null);
                    },
                  )
                }
              >
                <Check size={16} />{' '}
                {transition.action === 'dispatch'
                  ? 'Confirm dispatch'
                  : transition.action === 'collect'
                    ? 'Confirm collection'
                    : 'Confirm delivery'}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

function ReadyForm({
  order,
  workspace,
  defaults,
  endpoint,
  onSaved,
}: {
  order: OrderDetail;
  workspace: Workspace;
  defaults: HandoverDefaults;
  endpoint: string;
  onSaved: (data: HandoverResponse) => Promise<void>;
}) {
  const [address, setAddress] = useState(defaults.collection_address);
  const [instructions, setInstructions] = useState(defaults.collection_instructions);
  const [mode, setMode] = useState<DeliveryMode>(defaults.delivery_mode);
  const [fee, setFee] = useState((defaults.delivery_fee_cents / 100).toFixed(2));
  const [area, setArea] = useState(defaults.delivery_area);
  const initialStart = DateTime.now()
    .setZone(workspace.timezone)
    .plus({ hours: 1 })
    .startOf('hour');
  const [start, setStart] = useState(initialStart.toFormat("yyyy-MM-dd'T'HH:mm"));
  const [end, setEnd] = useState(initialStart.plus({ days: 7 }).toFormat("yyyy-MM-dd'T'HH:mm"));
  const [confirmed, setConfirmed] = useState(false);
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed) return;
    void action.run(async () => {
      try {
        const startISO = collectionISO(start, workspace.timezone);
        const endISO = collectionISO(end, workspace.timezone);
        if (new Date(endISO) <= new Date(startISO))
          throw new Error('The collection window must end after it starts.');
        return await post<HandoverResponse>(`${endpoint}/ready`, {
          expected_revision: order.accepted_revision?.number,
          collection_address: address.trim(),
          collection_instructions: instructions.trim(),
          collection_window_start: startISO,
          collection_window_end: endISO,
          delivery_mode: mode,
          delivery_fee_cents: mode === 'fixed' ? centsFromInput(fee) : 0,
          delivery_area: mode === 'unavailable' ? '' : area.trim(),
        });
      } catch (e) {
        throw new Error(handoverError(e));
      }
    }, onSaved);
  };
  return (
    <form className="form-stack handover-ready-form" onSubmit={submit}>
      <fieldset className="handover-fields" disabled={action.pending}>
        <legend>
          <MapPin size={18} /> Collection details
        </legend>
        <Field label="Collection address" htmlFor="ready-address">
          <textarea
            id="ready-address"
            rows={2}
            required
            minLength={3}
            maxLength={500}
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            placeholder="Business name, street, area and postcode"
          />
        </Field>
        <Field
          label="Opening hours & collection instructions"
          htmlFor="ready-instructions"
          hint="The customer sees these when choosing a collection time."
        >
          <textarea
            id="ready-instructions"
            rows={2}
            maxLength={1000}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            placeholder="Monday–Saturday, 10 am–6 pm. Ask for your order number at the counter."
          />
        </Field>
        <div className="form-row">
          <Field label="Collection available from" htmlFor="ready-start">
            <input
              id="ready-start"
              type="datetime-local"
              required
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </Field>
          <Field label="Collection available until" htmlFor="ready-end">
            <input
              id="ready-end"
              type="datetime-local"
              required
              min={start}
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </Field>
        </div>
        <p className="field-hint">
          All collection times are in {workspace.timezone.replaceAll('_', ' ')}. Choose a window
          ending within 30 days. Customers choose a future time inside this window and should follow
          your opening hours.
        </p>
      </fieldset>
      <fieldset className="handover-fields" disabled={action.pending}>
        <legend>
          <Truck size={18} /> Delivery options
        </legend>
        <Field label="Delivery policy" htmlFor="ready-mode">
          <select
            id="ready-mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as DeliveryMode)}
          >
            <option value="unavailable">Collection only</option>
            <option value="included">Delivery included · no extra charge</option>
            <option value="fixed">Delivery at a fixed fee</option>
            <option value="quote">Quote delivery after reviewing the address</option>
          </select>
        </Field>
        {mode === 'fixed' && (
          <Field label={`Delivery fee (${workspace.currency})`} htmlFor="ready-fee">
            <input
              id="ready-fee"
              type="number"
              min="0"
              max="1000000"
              step="0.01"
              required
              value={fee}
              onChange={(event) => setFee(event.target.value)}
            />
          </Field>
        )}
        {mode !== 'unavailable' && (
          <Field
            label="Delivery service area"
            htmlFor="ready-area"
            hint="Describe the areas you serve. You will check each requested address before confirming the fee."
          >
            <textarea
              id="ready-area"
              rows={2}
              required
              maxLength={500}
              value={area}
              onChange={(event) => setArea(event.target.value)}
              placeholder="For example: within 5 km of the shop. Other addresses need a separate arrangement."
            />
          </Field>
        )}
        {mode === 'included' && (
          <p className="field-hint">
            Use this when delivery is free or already included in the accepted price. No second
            delivery charge will be added.
          </p>
        )}
      </fieldset>
      <Notice title="Save a clear handover agreement.">
        Collection address, instructions and delivery rules become defaults for future orders. This
        order keeps its own fixed copy; its collection window is saved for this order only.
      </Notice>
      <label className="checkbox-line">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
          required
          disabled={action.pending || Boolean(order.hold_reason)}
        />
        <span>
          Production for <strong>revision {order.accepted_revision?.number}</strong> is finished,
          and the items are ready for collection or delivery.
        </span>
      </label>
      <ErrorNotice message={action.error} />
      <div className="form-actions">
        <Button
          type="submit"
          variant="primary"
          busy={action.pending}
          disabled={!confirmed || Boolean(order.hold_reason)}
        >
          <PackageCheck size={16} /> Mark ready & save handover
        </Button>
      </div>
      <p className="field-hint">
        You can create and copy the customer message after saving. Nothing is sent automatically.
      </p>
    </form>
  );
}

function QuoteModal({
  endpoint,
  initial,
  latest,
  timezone,
  onClose,
  onReload,
  onSaved,
}: {
  endpoint: string;
  initial: Handover;
  latest: Handover;
  timezone: string;
  onClose: () => void;
  onReload: () => void;
  onSaved: (data: HandoverResponse) => Promise<void>;
}) {
  const [fee, setFee] = useState(
    ((initial.delivery_fee_cents ?? initial.config.delivery_fee_cents) / 100).toFixed(2),
  );
  const [note, setNote] = useState(initial.quote_note ?? '');
  const [checked, setChecked] = useState(false);
  const action = useAction();
  const stale = latest.version !== initial.version;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!checked || stale) return;
    void action.run(async () => {
      try {
        return await post<HandoverResponse>(`${endpoint}/quote`, {
          expected_version: initial.version,
          delivery_fee_cents:
            initial.config.delivery_mode === 'quote'
              ? centsFromInput(fee)
              : initial.config.delivery_fee_cents,
          note: note.trim(),
        });
      } catch (e) {
        throw new Error(handoverError(e));
      }
    }, onSaved);
  };
  return (
    <Modal title="Review the delivery request" onClose={onClose}>
      <form className="modal-body form-stack" onSubmit={submit}>
        <HandoverDetails handover={initial} timezone={timezone} />
        <Notice title="Check the service area.">
          <p>{initial.config.delivery_area}</p>
          <p>
            Confirm you can serve the exact address before preparing a quote. The customer will
            approve the final total.
          </p>
        </Notice>
        <Field
          label={`Delivery fee (${initial.pricing.currency})`}
          htmlFor="quote-fee"
          hint={
            initial.config.delivery_mode === 'quote'
              ? 'This is an extra charge added to the existing order total.'
              : 'This amount is fixed by the delivery policy saved when the order was marked ready.'
          }
        >
          <input
            id="quote-fee"
            type="number"
            min="0"
            step="0.01"
            required
            value={fee}
            readOnly={initial.config.delivery_mode !== 'quote'}
            disabled={action.pending}
            onChange={(event) => setFee(event.target.value)}
          />
        </Field>
        <Field
          label="Delivery note for the customer"
          htmlFor="quote-note"
          hint="Include the expected delivery window or any agreed instructions."
          optional
        >
          <textarea
            id="quote-note"
            rows={3}
            maxLength={1000}
            value={note}
            disabled={action.pending}
            onChange={(event) => setNote(event.target.value)}
          />
        </Field>
        <label className="checkbox-line">
          <input
            type="checkbox"
            required
            checked={checked}
            disabled={action.pending || stale}
            onChange={(event) => setChecked(event.target.checked)}
          />
          <span>I checked the address and can arrange delivery for this fee.</span>
        </label>
        {stale && (
          <Notice tone="warning" title="The customer’s handover details changed.">
            Reload and check the latest address before quoting.
            <Button variant="ghost" onClick={onReload}>
              Reload handover
            </Button>
          </Notice>
        )}
        <ErrorNotice message={action.error} retry={onReload} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={action.pending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            busy={action.pending}
            disabled={!checked || stale || latest.on_hold}
          >
            Prepare quote for approval <ArrowRight size={16} />
          </Button>
        </div>
        <p className="field-hint">
          The quote will appear on the customer’s existing link. No message is sent automatically.
        </p>
      </form>
    </Modal>
  );
}

function ReceiptModal({
  endpoint,
  handover,
  sample,
  onClose,
  onSaved,
}: {
  endpoint: string;
  handover: Handover;
  sample: boolean;
  onClose: () => void;
  onSaved: (data: HandoverResponse) => Promise<void>;
}) {
  const [amount, setAmount] = useState(((handover.pricing.balance_cents ?? 0) / 100).toFixed(2));
  const [reference, setReference] = useState(sample ? 'Sample handover receipt' : '');
  const [confirmed, setConfirmed] = useState(false);
  const key = useRef(newIdempotencyKey());
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed) return;
    void action.run(async () => {
      try {
        return await post<HandoverResponse>(`${endpoint}/payment`, {
          amount_cents: centsFromInput(amount),
          reference: reference.trim(),
          idempotency_key: key.current,
        });
      } catch (e) {
        throw new Error(handoverError(e));
      }
    }, onSaved);
  };
  return (
    <Modal
      title={sample ? 'Record a sample balance receipt' : 'Record a received payment'}
      onClose={onClose}
    >
      <form className="modal-body form-stack" onSubmit={submit}>
        <Notice title={sample ? 'Sample financial record.' : 'Record money already received.'}>
          {sample
            ? 'Use a fictional receipt reference for this sample order.'
            : 'Use your bank, cash or payment-provider receipt. This form records it; it does not charge the customer.'}
        </Notice>
        <p className="muted">
          Outstanding balance:{' '}
          <strong>{money(handover.pricing.balance_cents, handover.pricing.currency)}</strong>,
          including the accepted handover fee.
        </p>
        <Field
          label={`Amount received (${handover.pricing.currency})`}
          htmlFor="handover-receipt-amount"
        >
          <input
            id="handover-receipt-amount"
            type="number"
            min="0.01"
            max={(handover.pricing.balance_cents ?? 0) / 100}
            step="0.01"
            required
            value={amount}
            disabled={action.pending}
            onChange={(event) => setAmount(event.target.value)}
          />
        </Field>
        <Field label="Receipt reference" htmlFor="handover-receipt-reference">
          <input
            id="handover-receipt-reference"
            required
            maxLength={200}
            value={reference}
            disabled={action.pending}
            onChange={(event) => setReference(event.target.value)}
          />
        </Field>
        <label className="checkbox-line">
          <input
            type="checkbox"
            required
            checked={confirmed}
            disabled={action.pending}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span>
            {sample
              ? 'I am recording a sample receipt for this demonstration.'
              : 'I checked this receipt and the money has been received.'}
          </span>
        </label>
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={action.pending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending} disabled={!confirmed}>
            Record received payment
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function HandoverShareModal({
  endpoint,
  handover,
  timezone,
  onClose,
  onShared,
}: {
  endpoint: string;
  handover: Handover;
  timezone: string;
  onClose: () => void;
  onShared: () => Promise<void>;
}) {
  const [share, setShare] = useState<HandoverShare | null>(null);
  const [copied, setCopied] = useState<'link' | 'message' | null>(null);
  const [copyError, setCopyError] = useState<string | null>(null);
  const linkInput = useRef<HTMLInputElement>(null);
  const messageInput = useRef<HTMLTextAreaElement>(null);
  const action = useAction();
  const copy = async (kind: 'link' | 'message') => {
    if (!share) return;
    try {
      await navigator.clipboard.writeText(kind === 'link' ? share.url : share.notification_text);
      setCopied(kind);
      setCopyError(null);
    } catch {
      (kind === 'link' ? linkInput.current : messageInput.current)?.select();
      setCopyError('Your browser did not allow automatic copying. Select and copy the text below.');
    }
  };
  return (
    <Modal
      title={
        share
          ? 'Your customer message is ready.'
          : handover.link_active
            ? 'Create a replacement customer link?'
            : 'Prepare the customer’s next step'
      }
      onClose={onClose}
    >
      <div className="modal-body form-stack">
        {!share ? (
          <>
            <Notice
              title={
                handover.link_active
                  ? 'The previous link will stop working.'
                  : 'A private page for this order.'
              }
            >
              {handover.link_active
                ? 'Creating a replacement preserves the customer’s choice and accepted quote, but revokes their old link. Share the replacement with them.'
                : 'The customer can choose collection or request delivery, accept the exact quote, and follow the handover status without signing in.'}
            </Notice>
            <p className="muted">
              OrderToWork prepares the link and notification text. You share them using your usual
              email or messaging channel.
            </p>
            <ErrorNotice message={action.error} />
            <div className="form-actions">
              <Button onClick={onClose} disabled={action.pending}>
                Cancel
              </Button>
              <Button
                variant="primary"
                busy={action.pending}
                onClick={() =>
                  void action.run(
                    () => post<HandoverShare>(`${endpoint}/share`),
                    async (result) => {
                      setShare(result);
                      await onShared();
                    },
                  )
                }
              >
                <Link2 size={16} />{' '}
                {handover.link_active ? 'Create replacement link' : 'Create customer link'}
              </Button>
            </div>
          </>
        ) : (
          <>
            <Notice tone="success" title="Ready to copy and share.">
              No email or message has been sent. Share this private link only with the customer;
              anyone with it can view and respond to this handover.
            </Notice>
            <Field
              label="Private customer link"
              htmlFor="handover-share-link"
              hint={`Expires ${dateTime(share.expires_at, timezone)}.`}
            >
              <div className="copy-field">
                <input
                  id="handover-share-link"
                  ref={linkInput}
                  readOnly
                  value={share.url}
                  onFocus={(event) => event.target.select()}
                />
                <Button onClick={() => void copy('link')}>
                  <Copy size={15} /> {copied === 'link' ? 'Copied' : 'Copy link'}
                </Button>
              </div>
            </Field>
            <Field label="Prepared notification text" htmlFor="handover-share-message">
              <textarea
                id="handover-share-message"
                ref={messageInput}
                readOnly
                rows={7}
                value={share.notification_text}
                onFocus={(event) => event.target.select()}
              />
            </Field>
            <div className="action-cluster">
              <Button variant="primary" onClick={() => void copy('message')}>
                <Copy size={15} /> {copied === 'message' ? 'Message copied' : 'Copy message'}
              </Button>
              <a
                className="button button-secondary"
                href={share.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open customer view <ExternalLink size={15} />
              </a>
            </div>
            <ErrorNotice message={copyError} />
            {copied && (
              <p className="field-hint" role="status">
                {copied === 'message' ? 'Message' : 'Link'} copied. Paste it into your usual channel
                to share it.
              </p>
            )}
            <div className="form-actions">
              <Button onClick={onClose}>Done</Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

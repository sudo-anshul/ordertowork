import {
  ArrowRight,
  Check,
  CheckCheck,
  MapPin,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
  Truck,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import {
  HandoverBadge,
  HandoverDetails,
  HandoverPrice,
  HandoverProgress,
} from '../components/handover-summary';
import { Brand, Button, ErrorNotice, Field, Loading, Notice } from '../components/ui';
import { api, ApiError, errorMessage, post } from '../lib/api';
import { dateTime, initials, money, pickupInput, titleCase } from '../lib/format';
import { collectionISO, deliveryDescription, handoverError } from '../lib/handover';
import type { CustomerHandover, Handover } from '../lib/types';

export function CustomerHandoverPage() {
  const { token } = useParams();
  return <CustomerHandoverView key={token} token={token ?? ''} />;
}

function CustomerHandoverView({ token }: { token: string }) {
  const path = `/handover/${encodeURIComponent(token)}`;
  const [data, setData] = useState<CustomerHandover | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadStatus, setLoadStatus] = useState(0);
  const [editing, setEditing] = useState<boolean | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const request = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const refresh = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const result = await api<CustomerHandover>(path, { signal: controller.signal });
      if (controller.signal.aborted) return null;
      setData((current) =>
        current && current.handover.version > result.handover.version ? current : result,
      );
      setEditing((current) =>
        current === null ? result.handover.status === 'awaiting_choice' : current,
      );
      setLoadError(null);
      setLoadStatus(0);
      return result;
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return null;
      if (controller.signal.aborted) return null;
      setLoadError(errorMessage(e));
      setLoadStatus(e instanceof ApiError ? e.status : 0);
      if (e instanceof ApiError && [404, 410].includes(e.status)) setData(null);
      return null;
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && !lock.current) void refresh();
    }, 10000);
    return () => {
      request.current?.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    document.title = data
      ? `${data.business.name} · Your order handover`
      : 'OrderToWork · Your order handover';
  }, [data?.business.name]);
  const mutate = async (suffix: string, body: unknown) => {
    if (lock.current) return;
    lock.current = true;
    request.current?.abort();
    setPending(true);
    setError(null);
    try {
      const result = await post<CustomerHandover>(`${path}/${suffix}`, body);
      setData(result);
      setEditing(false);
      setConflict(false);
      heading.current?.focus({ preventScroll: true });
    } catch (e) {
      setError(handoverError(e));
      if (e instanceof ApiError && e.status === 409) setConflict(true);
      if (e instanceof ApiError && [404, 410].includes(e.status)) await refresh();
    } finally {
      lock.current = false;
      setPending(false);
    }
  };
  const reloadForm = async () => {
    const latest = await refresh();
    if (!latest) return;
    setError(null);
    setConflict(false);
    setFormKey((current) => current + 1);
  };
  if (loading)
    return (
      <div className="customer-page">
        <Loading label="Opening your collection and delivery options…" />
      </div>
    );
  if (!data)
    return (
      <div className="customer-page">
        <div className="customer-outer">
          <div className="customer-brand">
            <Brand />
          </div>
          <main className="customer-card customer-unavailable">
            <div className="empty-icon">
              <ShieldCheck size={28} />
            </div>
            <h1>
              {[404, 410].includes(loadStatus)
                ? 'This handover link is no longer active.'
                : 'We couldn’t open your handover.'}
            </h1>
            <p>{loadError}</p>
            {[404, 410].includes(loadStatus) ? (
              <p>
                Ask the business for a current handover link. Your earlier order agreement is
                preserved.
              </p>
            ) : (
              <Button variant="primary" onClick={() => void refresh()}>
                Try again
              </Button>
            )}
          </main>
        </div>
      </div>
    );
  const { business, order, handover } = data;
  const timezone = handover.config.timezone ?? business.timezone;
  const terminal = ['out_for_delivery', 'collected', 'delivered'].includes(handover.status);
  const complete = ['collected', 'delivered'].includes(handover.status);
  const canChange = !terminal && handover.pricing.paid_cents <= handover.pricing.order_total_cents;
  const choose =
    (Boolean(editing) || handover.status === 'awaiting_choice') && !terminal && canChange;
  const titles: Record<Handover['status'], string> = {
    awaiting_choice: 'Your order is ready.',
    delivery_requested: 'Your delivery request is recorded.',
    quote_ready: 'A clear delivery quote, for your approval.',
    confirmed:
      handover.method === 'collection'
        ? 'Your collection is confirmed.'
        : 'Your delivery is confirmed.',
    out_for_delivery: 'Your order is on its way.',
    collected: 'Collected. All yours.',
    delivered: 'Your order has been delivered.',
  };
  return (
    <div className="customer-page customer-handover-page">
      <a className="skip-link" href="#handover-main">
        Skip to handover
      </a>
      {order.is_demo && (
        <div className="customer-demo">
          Sample order · Use fictional details. No real purchase or payment is made.
        </div>
      )}
      <div className="customer-outer">
        <header className="customer-brand">
          <div className="business-mark">{initials(business.name).slice(0, 1)}</div>
          <div>
            <strong>{business.name}</strong>
            <span>
              {order.number} · Revision {order.revision}
            </span>
          </div>
        </header>
        <ErrorNotice
          title="The latest status could not be loaded."
          message={loadError}
          retry={() => void refresh()}
        />
        <main id="handover-main" className="customer-card">
          <div className="receipt-symbol">
            {complete ? (
              <CheckCheck size={30} strokeWidth={1.5} />
            ) : handover.status === 'out_for_delivery' ? (
              <Truck size={30} strokeWidth={1.5} />
            ) : (
              <PackageCheck size={30} strokeWidth={1.5} />
            )}
          </div>
          <p className="eyebrow">The final step, clearly agreed</p>
          <h1 ref={heading} tabIndex={-1}>
            {handover.on_hold ? 'Your handover is on hold.' : titles[handover.status]}
          </h1>
          <p>
            {complete
              ? `Thank you, ${order.customer_name}. ${business.name} has recorded the completed handover.`
              : `Hello ${order.customer_name}. ${choose ? 'Choose how you would like to receive your finished order.' : 'Your latest collection or delivery details are below.'}`}
          </p>
          <div className="handover-customer-order">
            <strong>
              {order.quantity} {order.product_name.toLowerCase()}
            </strong>
            <span>
              {titleCase(order.variant)} · Revision {order.revision} agreed
            </span>
            <HandoverBadge handover={handover} />
          </div>
          <HandoverProgress handover={handover} customer />
          {handover.on_hold && (
            <Notice
              tone="warning"
              title="Please contact the business before travelling or arranging delivery."
            >
              {business.name} needs to resolve a hold. Your current choice and any recorded payments
              remain visible below.
            </Notice>
          )}
          <ErrorNotice message={error} />
          {choose ? (
            <CustomerChoiceForm
              key={formKey}
              data={data}
              pending={pending}
              conflict={conflict}
              onReload={() => void reloadForm()}
              onCancel={
                handover.status !== 'awaiting_choice'
                  ? () => {
                      setEditing(false);
                      setError(null);
                      setConflict(false);
                    }
                  : undefined
              }
              onSubmit={(body) => mutate('choose', body)}
            />
          ) : (
            <>
              {handover.status === 'delivery_requested' && (
                <Notice title="Delivery is requested, not confirmed yet.">
                  {business.name} will check your address and prepare the exact delivery fee. Return
                  to this same link to review and accept the quote.
                </Notice>
              )}
              {handover.status === 'quote_ready' && (
                <Notice title="Review the delivery fee and final balance.">
                  The business checked your address. Confirm the exact fee below to agree to
                  delivery.
                </Notice>
              )}
              {handover.status === 'confirmed' && (
                <Notice
                  tone="success"
                  title={
                    handover.method === 'collection'
                      ? 'Your collection choice is saved.'
                      : 'Your delivery quote is accepted.'
                  }
                >
                  {(handover.pricing.balance_cents ?? 0) > 0
                    ? `Arrange the remaining ${money(handover.pricing.balance_cents, business.currency)} directly with ${business.name} before collection or dispatch.`
                    : 'The full balance is recorded. Follow the agreed collection or delivery arrangements below.'}
                </Notice>
              )}
              {handover.status === 'out_for_delivery' && (
                <Notice tone="success" title="Dispatch recorded by the business.">
                  Contact {business.name} for delivery updates. This page will show when delivery is
                  recorded.
                </Notice>
              )}
              <HandoverDetails handover={handover} timezone={timezone} />
              {handover.quote_note && (
                <div className="handover-quote-note">
                  <strong>A note from {business.name}</strong>
                  <p>{handover.quote_note}</p>
                </div>
              )}
              <HandoverPrice
                pricing={handover.pricing}
                method={handover.method}
                quote={handover.status === 'quote_ready'}
              />
              {handover.status === 'quote_ready' && (
                <CustomerQuoteApproval
                  key={formKey}
                  handover={handover}
                  pending={pending}
                  conflict={conflict}
                  onReload={() => void reloadForm()}
                  onSubmit={(body) => mutate('accept-quote', body)}
                />
              )}
              {!terminal &&
                (canChange ? (
                  <div className="customer-actions">
                    <Button
                      disabled={pending || handover.on_hold}
                      onClick={() => {
                        setEditing(true);
                        setFormKey((current) => current + 1);
                        setError(null);
                        setConflict(false);
                      }}
                    >
                      Change collection or delivery
                    </Button>
                  </div>
                ) : (
                  <p className="customer-legal">
                    A delivery payment is already recorded. Contact {business.name} to change
                    arrangements or discuss a refund.
                  </p>
                ))}
            </>
          )}
          <p className="customer-legal">
            <ShieldCheck size={14} /> Your choice and any delivery quote are recorded for this
            order. No payment is taken on this page.
          </p>
          <p className="customer-expiry">
            Link expires {dateTime(data.expires_at, timezone)}. Keep this link private.
          </p>
        </main>
        <footer className="customer-footer">
          <span>
            Powered by <strong>OrderToWork</strong>
          </span>
          <span>Your order, clearly agreed.</span>
        </footer>
      </div>
    </div>
  );
}

function CustomerChoiceForm({
  data,
  pending,
  conflict,
  onReload,
  onCancel,
  onSubmit,
}: {
  data: CustomerHandover;
  pending: boolean;
  conflict: boolean;
  onReload: () => void;
  onCancel?: () => void;
  onSubmit: (body: unknown) => Promise<void>;
}) {
  const { handover, business } = data;
  const timezone = handover.config.timezone ?? business.timezone;
  const [version] = useState(handover.version);
  const [method, setMethod] = useState<'collection' | 'delivery'>(handover.method ?? 'collection');
  const [time, setTime] = useState(pickupInput(handover.collection_at ?? undefined, timezone));
  const [address, setAddress] = useState(handover.delivery_address ?? '');
  const [phone, setPhone] = useState(handover.contact_phone ?? '');
  const [note, setNote] = useState(handover.customer_note ?? '');
  const [consent, setConsent] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);
  const stale = conflict || version !== handover.version;
  const changeMethod = (value: 'collection' | 'delivery') => {
    setMethod(value);
    setConsent(false);
    setValidation(null);
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!consent || stale || pending) return;
    setValidation(null);
    try {
      const when = method === 'collection' ? collectionISO(time, timezone) : null;
      if (when && new Date(when).getTime() <= Date.now())
        throw new Error('Choose a future collection time within the available window.');
      await onSubmit({
        expected_version: version,
        method,
        collection_at: when,
        delivery_address: method === 'delivery' ? address.trim() : '',
        contact_phone: method === 'delivery' ? phone.trim() : '',
        customer_note: note.trim(),
        consent: true,
      });
    } catch (e) {
      setValidation(errorMessage(e));
    }
  };
  return (
    <form className="form-stack customer-choice-form" onSubmit={(event) => void submit(event)}>
      <fieldset className="handover-choice" disabled={pending || handover.on_hold}>
        <legend>How would you like to receive your order?</legend>
        <label className={method === 'collection' ? 'selected' : ''}>
          <input
            type="radio"
            name="handover-method"
            value="collection"
            checked={method === 'collection'}
            onChange={() => changeMethod('collection')}
          />
          <MapPin size={22} />
          <span>
            <strong>I’ll collect it</strong>
            <small>No collection fee</small>
          </span>
        </label>
        {handover.config.delivery_mode !== 'unavailable' && (
          <label className={method === 'delivery' ? 'selected' : ''}>
            <input
              type="radio"
              name="handover-method"
              value="delivery"
              checked={method === 'delivery'}
              onChange={() => changeMethod('delivery')}
            />
            <Truck size={22} />
            <span>
              <strong>Please deliver it</strong>
              <small>{deliveryDescription(handover.config, business.currency)}</small>
            </span>
          </label>
        )}
      </fieldset>
      {stale && (
        <Notice tone="warning" title="Your handover details changed while you were here.">
          Your typed details are still shown. Reload the latest choices before confirming; reloading
          will replace this draft.
          <Button type="button" variant="ghost" onClick={onReload}>
            <RefreshCw size={15} /> Reload latest details
          </Button>
        </Notice>
      )}
      <fieldset className="handover-fields" disabled={pending || handover.on_hold}>
        <legend className="sr-only">
          {method === 'collection' ? 'Collection details' : 'Delivery request details'}
        </legend>
        {method === 'collection' ? (
          <>
            <div className="handover-collection-info">
              <h3>Collect from</h3>
              <p className="preserve-lines">{handover.config.collection_address}</p>
              {handover.config.collection_instructions && (
                <p className="preserve-lines">{handover.config.collection_instructions}</p>
              )}
              <p className="field-hint">
                Available from {dateTime(handover.config.collection_window_start, timezone)} until{' '}
                {dateTime(handover.config.collection_window_end, timezone)}.
              </p>
            </div>
            <Field
              label="Your collection time"
              htmlFor="customer-collection-time"
              hint={`Choose within the available window and follow the opening hours above. Times are in ${timezone.replaceAll('_', ' ')}.`}
            >
              <input
                id="customer-collection-time"
                type="datetime-local"
                required
                min={pickupInput(handover.config.collection_window_start, timezone)}
                max={pickupInput(handover.config.collection_window_end, timezone)}
                value={time}
                onChange={(event) => {
                  setTime(event.target.value);
                  setConsent(false);
                }}
              />
            </Field>
          </>
        ) : (
          <>
            <Notice title="The business checks your address first.">
              <p>Service area: {handover.config.delivery_area}</p>
              <p>
                {handover.config.delivery_mode === 'quote'
                  ? 'You will see and approve the final delivery fee after address review.'
                  : `${deliveryDescription(handover.config, business.currency)}. You will confirm the exact total after the business reviews your address.`}
              </p>
            </Notice>
            <Field
              label="Full delivery address"
              htmlFor="customer-delivery-address"
              hint="Include your street, area, city and postcode."
            >
              <textarea
                id="customer-delivery-address"
                autoComplete="street-address"
                rows={3}
                required
                minLength={5}
                maxLength={1000}
                value={address}
                onChange={(event) => {
                  setAddress(event.target.value);
                  setConsent(false);
                }}
                placeholder="Street address, area, city and postcode"
              />
            </Field>
            <Field label="Contact phone number" htmlFor="customer-delivery-phone">
              <input
                id="customer-delivery-phone"
                type="tel"
                autoComplete="tel"
                required
                minLength={5}
                maxLength={40}
                value={phone}
                onChange={(event) => {
                  setPhone(event.target.value);
                  setConsent(false);
                }}
                placeholder="A number the business can reach you on"
              />
            </Field>
          </>
        )}
        <Field
          label={method === 'collection' ? 'Collection note' : 'Delivery instructions'}
          htmlFor="customer-handover-note"
          optional
        >
          <textarea
            id="customer-handover-note"
            rows={2}
            maxLength={1000}
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
              setConsent(false);
            }}
            placeholder={
              method === 'collection'
                ? 'Anything the business should know before you arrive.'
                : 'Building access or other details for delivery.'
            }
          />
        </Field>
      </fieldset>
      <div className="handover-choice-price">
        <span>Agreed order total</span>
        <strong>{money(handover.pricing.order_total_cents, business.currency)}</strong>
        <span>Payments recorded</span>
        <strong>{money(handover.pricing.paid_cents, business.currency)}</strong>
        {method === 'collection' ? (
          <>
            <span>Collection fee</span>
            <strong>No extra charge</strong>
            <span>Balance before collection</span>
            <strong>
              {money(
                Math.max(0, handover.pricing.order_total_cents - handover.pricing.paid_cents),
                business.currency,
              )}
            </strong>
          </>
        ) : (
          <>
            <span>Delivery terms</span>
            <strong>{deliveryDescription(handover.config, business.currency)}</strong>
          </>
        )}
      </div>
      <label className="customer-consent">
        <input
          type="checkbox"
          required
          checked={consent}
          disabled={pending || stale || handover.on_hold}
          onChange={(event) => setConsent(event.target.checked)}
        />
        <span>
          {method === 'collection'
            ? 'I confirm collection at the address and time above, with no extra collection fee. I will settle the remaining balance directly with the business.'
            : 'I request delivery to the address above. I understand delivery is subject to review and I must accept the final quote before dispatch.'}
        </span>
      </label>
      <ErrorNotice message={validation} />
      <div className="customer-actions">
        <Button
          type="submit"
          variant="primary"
          busy={pending}
          disabled={!consent || stale || handover.on_hold}
        >
          {method === 'collection' ? 'Confirm collection' : 'Request delivery'}{' '}
          <ArrowRight size={16} />
        </Button>
        {onCancel && (
          <Button type="button" onClick={onCancel} disabled={pending}>
            Keep current choice
          </Button>
        )}
      </div>
    </form>
  );
}

function CustomerQuoteApproval({
  handover,
  pending,
  conflict,
  onReload,
  onSubmit,
}: {
  handover: Handover;
  pending: boolean;
  conflict: boolean;
  onReload: () => void;
  onSubmit: (body: unknown) => Promise<void>;
}) {
  const [version] = useState(handover.version);
  const [hash] = useState(handover.quote_hash);
  const [consent, setConsent] = useState(false);
  const stale = conflict || version !== handover.version || hash !== handover.quote_hash;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!consent || stale || pending || !hash) return;
    void onSubmit({ expected_version: version, quote_hash: hash, consent: true });
  };
  return (
    <form onSubmit={submit} className="customer-quote-approval">
      {stale && (
        <Notice tone="warning" title="This delivery quote has changed.">
          Reload and review the new fee and address before accepting.
          <Button type="button" variant="ghost" onClick={onReload}>
            <RefreshCw size={15} /> Reload quote
          </Button>
        </Notice>
      )}
      <label className="customer-consent">
        <input
          type="checkbox"
          required
          checked={consent && !stale}
          disabled={pending || stale || handover.on_hold}
          onChange={(event) => setConsent(event.target.checked)}
        />
        <span>
          I accept delivery to the address shown, with a delivery fee of{' '}
          <strong>{money(handover.pricing.delivery_fee_cents, handover.pricing.currency)}</strong>{' '}
          and a final total of{' '}
          <strong>{money(handover.pricing.total_cents, handover.pricing.currency)}</strong>. I will
          arrange the remaining payment directly with the business.
        </span>
      </label>
      <div className="customer-actions">
        <Button
          type="submit"
          variant="primary"
          busy={pending}
          disabled={!consent || stale || handover.on_hold || !hash}
        >
          <Check size={16} /> Accept delivery quote
        </Button>
      </div>
    </form>
  );
}

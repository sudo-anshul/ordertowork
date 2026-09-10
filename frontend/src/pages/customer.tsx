import { ArrowRight, Check, CheckCheck, ClipboardCheck, ShieldCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useParams } from 'react-router-dom';
import { Specification, TermsDiff } from '../components/orders';
import { Brand, Button, ErrorNotice, Field, Loading, Notice } from '../components/ui';
import { api, ApiError, errorMessage, post } from '../lib/api';
import { dateTime, initials, money } from '../lib/format';
import type { CustomerOrder } from '../lib/types';

export function CustomerPage() {
  const { token } = useParams();
  const path = `/customer/${encodeURIComponent(token ?? '')}`;
  const [data, setData] = useState<CustomerOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadStatus, setLoadStatus] = useState(0);
  const [consent, setConsent] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [body, setBody] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [terminal, setTerminal] = useState<'availability' | 'change' | 'stale' | null>(null);
  const lock = useRef(false);
  const stateHeading = useRef<HTMLHeadingElement>(null);
  const refresh = async () => {
    try {
      const result = await api<CustomerOrder>(path);
      setData(result);
      setLoadError(null);
      document.title = `${result.business.name} · Review your order`;
    } catch (e) {
      setLoadError(errorMessage(e));
      setLoadStatus(e instanceof ApiError ? e.status : 0);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    setLoading(true);
    setData(null);
    setTerminal(null);
    setConsent(false);
    void refresh();
  }, [path]);
  useEffect(() => {
    if (terminal || data?.status === 'approved') stateHeading.current?.focus();
  }, [terminal, data?.status]);
  const approve = async (event: FormEvent) => {
    event.preventDefault();
    if (!data || !consent || lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await post<{ status: 'approved' | 'already_approved'; top_up_cents: number }>(
        path + '/approve',
        { terms_hash: data.revision.terms_hash, consent: true },
      );
      setData((current) =>
        current ? { ...current, status: 'approved', top_up_cents: result.top_up_cents } : current,
      );
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'availability_changed') setTerminal('availability');
      else if (e instanceof ApiError && e.status === 410) setTerminal('stale');
      else {
        setError(errorMessage(e));
        setConsent(false);
      }
    } finally {
      lock.current = false;
      setPending(false);
    }
  };
  const requestChange = async (event: FormEvent) => {
    event.preventDefault();
    if (!body.trim() || lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      await post(path + '/request-change', { body: body.trim() });
      setTerminal('change');
    } catch (e) {
      if (e instanceof ApiError && e.status === 410) setTerminal('stale');
      else setError(errorMessage(e));
    } finally {
      lock.current = false;
      setPending(false);
    }
  };
  if (loading)
    return (
      <div className="customer-page">
        <Loading label="Opening your order…" />
      </div>
    );
  if (!data)
    return (
      <div className="customer-page">
        <div className="customer-outer">
          <div className="customer-brand">
            <Brand />
          </div>
          <section className="customer-card customer-unavailable">
            <div className="empty-icon">
              <ShieldCheck size={28} />
            </div>
            <h1>
              {loadStatus === 410
                ? 'This review link is no longer active.'
                : 'We couldn’t open your order.'}
            </h1>
            <p>{loadError}</p>
            {loadStatus === 410 ? (
              <p>
                Ask the business for its latest review link. Opening this page does not approve or
                change an order.
              </p>
            ) : (
              <Button variant="primary" onClick={() => void refresh()}>
                Try again
              </Button>
            )}
          </section>
        </div>
      </div>
    );
  const { revision, business, order } = data;
  const approved = data.status === 'approved';
  return (
    <div className="customer-page">
      <a className="skip-link" href="#customer-main">
        Skip to order
      </a>
      {order.is_demo && (
        <div className="customer-demo">
          Sample order · This is demonstration data, not a real customer purchase.
        </div>
      )}
      <div className="customer-outer">
        <header className="customer-brand">
          <div className="business-mark">{initials(business.name).slice(0, 1)}</div>
          <div>
            <strong>{business.name}</strong>
            <span>
              {order.number} ·{' '}
              {approved
                ? `Revision ${revision.number} confirmed`
                : terminal === 'availability'
                  ? 'Change not confirmed'
                  : terminal === 'change'
                    ? 'Change requested'
                    : `Revision ${revision.number} proposal`}
            </span>
          </div>
        </header>
        <ErrorNotice
          title="The latest receipt could not be loaded."
          message={loadError}
          retry={() => void refresh()}
        />
        <main id="customer-main" className="customer-card">
          {terminal === 'availability' ? (
            <>
              <div className="receipt-symbol warning">
                <ShieldCheck size={28} />
              </div>
              <h1 ref={stateHeading} tabIndex={-1}>
                The shop needs to check a new option.
              </h1>
              <p>
                Availability changed before this revision could be reserved.{' '}
                {data.previous_terms
                  ? 'Your earlier confirmed order and its reservations remain in place.'
                  : 'This new order has not been confirmed, and no new resources were reserved.'}
              </p>
              <Notice tone="warning" title="This change is not confirmed.">
                Contact {business.name} for a newly checked proposal. No payment was taken through
                this page.
              </Notice>
            </>
          ) : terminal === 'stale' ? (
            <>
              <div className="receipt-symbol warning">
                <ShieldCheck size={28} />
              </div>
              <h1 ref={stateHeading} tabIndex={-1}>
                There’s a newer decision to review.
              </h1>
              <p>
                This link can no longer approve the order. Ask {business.name} for the latest
                proposal.
              </p>
              <p>
                {data.previous_terms
                  ? 'Your earlier agreement remains in place.'
                  : 'This link did not confirm a new order.'}
              </p>
            </>
          ) : terminal === 'change' ? (
            <>
              <div className="receipt-symbol">
                <MessageIcon />
              </div>
              <h1 ref={stateHeading} tabIndex={-1}>
                Your change request is recorded.
              </h1>
              <p>{business.name} has your reply and needs to prepare a new proposal.</p>
              <blockquote className="customer-request-quote">{body.trim()}</blockquote>
              <Notice
                title={
                  data.previous_terms
                    ? 'Your original order remains confirmed.'
                    : 'Your new order is not confirmed yet.'
                }
              >
                This link can no longer approve the previous proposal. The shop will share a new
                review link when the revised terms are ready.
              </Notice>
            </>
          ) : approved ? (
            <>
              <div className="receipt-symbol">
                <CheckCheck size={30} strokeWidth={1.5} />
              </div>
              <p className="eyebrow">Agreement recorded</p>
              <h1 ref={stateHeading} tabIndex={-1}>
                {data.previous_terms
                  ? 'Your revised order is confirmed.'
                  : 'Your order is confirmed.'}
              </h1>
              <p>
                Thank you, {order.customer_name}. The agreed specification and production resources
                are recorded for revision {revision.number}.
              </p>
              <div className="customer-receipt-summary">
                <strong>
                  {revision.terms.quantity} {revision.terms.product_name.toLowerCase()}
                </strong>
                <span>{dateTime(revision.terms.pickup_at, business.timezone, true)}</span>
                <small>{business.timezone.replaceAll('_', ' ')}</small>
              </div>
              <TermsDiff
                before={null}
                after={revision.terms}
                timezone={business.timezone}
                customer
              />
              <Specification terms={revision.terms} />
              <CustomerPrice data={data} receipt />
              <p className="customer-legal">
                Your agreement applies to this version. Any further material change requires a new
                review.
              </p>
            </>
          ) : (
            <>
              <p className="eyebrow">An agreement you can see clearly</p>
              <h1>A clear look at your {data.previous_terms ? 'revised' : 'new'} order.</h1>
              <p>
                Please review the terms below.{' '}
                {data.previous_terms
                  ? 'Your original order remains confirmed until this revision is approved and availability is checked again.'
                  : 'Your order is confirmed only after you approve this version and availability is checked.'}
              </p>
              <TermsDiff
                before={data.previous_terms}
                after={revision.terms}
                timezone={business.timezone}
                customer
              />
              <Specification terms={revision.terms} />
              <CustomerPrice data={data} />
              <ErrorNotice message={error} />
              {requesting ? (
                <form className="customer-request-form" onSubmit={requestChange}>
                  <Field label="What would you like to change?" htmlFor="customer-change">
                    <textarea
                      id="customer-change"
                      autoFocus
                      rows={4}
                      required
                      value={body}
                      maxLength={10000}
                      onChange={(e) => setBody(e.target.value)}
                      placeholder="Tell the shop what should be different."
                    />
                  </Field>
                  <div className="customer-actions">
                    <Button type="submit" variant="primary" busy={pending} disabled={!body.trim()}>
                      Send change request <ArrowRight size={16} />
                    </Button>
                    <Button
                      type="button"
                      disabled={pending}
                      onClick={() => {
                        setRequesting(false);
                        setError(null);
                      }}
                    >
                      Back to review
                    </Button>
                  </div>
                </form>
              ) : (
                <form onSubmit={approve}>
                  <label className="customer-consent">
                    <input
                      type="checkbox"
                      checked={consent}
                      onChange={(e) => setConsent(e.target.checked)}
                      required
                    />
                    <span>
                      I approve the quantity, specification, total price, and pickup shown for{' '}
                      <strong>revision {revision.number}</strong>.
                    </span>
                  </label>
                  <div className="customer-actions">
                    <Button variant="primary" type="submit" busy={pending}>
                      Approve revision {revision.number} <ArrowRight size={17} />
                    </Button>
                    <Button
                      type="button"
                      disabled={pending}
                      onClick={() => {
                        setRequesting(true);
                        setError(null);
                      }}
                    >
                      Request a change
                    </Button>
                  </div>
                </form>
              )}
              <p className="customer-legal">
                <ShieldCheck size={14} /> Approval is recorded for this exact version. Availability
                is checked before it is confirmed.
              </p>
              <p className="customer-expiry">
                Review link expires {dateTime(data.expires_at, business.timezone)}.
              </p>
            </>
          )}
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

function MessageIcon() {
  return <ClipboardCheck size={30} strokeWidth={1.5} />;
}
function CustomerPrice({ data, receipt = false }: { data: CustomerOrder; receipt?: boolean }) {
  const terms = data.revision.terms;
  return (
    <div className="customer-price">
      <div className="customer-price-main">
        <span>{receipt ? 'Order total' : 'Proposed order total'}</span>
        <strong>{money(terms.total_cents, data.business.currency)}</strong>
      </div>
      {data.previous_terms && data.previous_terms.total_cents !== terms.total_cents && (
        <p>
          Previously {money(data.previous_terms.total_cents, data.business.currency)} ·{' '}
          {terms.total_cents > data.previous_terms.total_cents ? 'increase' : 'decrease'} of{' '}
          {money(
            Math.abs(terms.total_cents - data.previous_terms.total_cents),
            data.business.currency,
          )}
        </p>
      )}
      {terms.rush_fee_cents > 0 && (
        <p>Includes {money(terms.rush_fee_cents, data.business.currency)} configured rush fee.</p>
      )}
      <div className="customer-deposit">
        <span>Deposit already recorded</span>
        <strong>{money(data.deposit_paid_cents, data.business.currency)}</strong>
      </div>
      <div className="customer-deposit">
        <span>
          {data.top_up_cents > 0
            ? 'Additional deposit before production'
            : 'Additional deposit needed'}
        </span>
        <strong>{money(data.top_up_cents, data.business.currency)}</strong>
      </div>
      {receipt && data.top_up_cents <= 0 && (
        <p className="deposit-met">
          <Check size={15} /> Required deposit recorded.
        </p>
      )}
      <p className="customer-price-note">
        {data.top_up_cents > 0
          ? `Arrange the additional deposit with ${data.business.name}. This page does not take payment.`
          : 'The shop tracks production readiness separately from your approval.'}
      </p>
    </div>
  );
}

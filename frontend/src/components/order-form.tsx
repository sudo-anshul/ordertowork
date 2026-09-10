import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import type { Product, Terms } from '../lib/types';
import { errorMessage } from '../lib/api';
import { money, pickupISO, pickupInput, titleCase } from '../lib/format';
import { Button, ErrorNotice, Field, Notice } from './ui';

export interface OrderInput {
  customer_name?: string;
  customer_email?: string;
  product_id: string;
  quantity: number;
  variant: string;
  sizes: Record<string, number>;
  pickup_at: string;
  specification: Record<string, string>;
  label?: string;
}
export function OrderForm({
  products,
  timezone,
  initial,
  customer,
  onSubmit,
  busy,
  error,
  onCancel,
}: {
  products: Product[];
  timezone: string;
  initial?: Terms | null;
  customer?: boolean;
  onSubmit: (data: OrderInput) => void;
  busy: boolean;
  error: string | null;
  onCancel?: () => void;
}) {
  const initialProduct =
    products.find((product) => product.id === initial?.product_id) ?? products[0];
  const [productId, setProductId] = useState(initialProduct?.id ?? '');
  const product = products.find((item) => item.id === productId);
  const [customerName, setCustomerName] = useState('');
  const [email, setEmail] = useState('');
  const [quantity, setQuantity] = useState(String(initial?.quantity ?? 1));
  const [variant, setVariant] = useState(initial?.variant ?? initialProduct?.variants[0] ?? '');
  const [sizes, setSizes] = useState<Record<string, number>>(
    initial?.sizes ??
      Object.fromEntries(
        (initialProduct?.sizes ?? []).map((size, index) => [size, index === 0 ? 1 : 0]),
      ),
  );
  const [pickup, setPickup] = useState(pickupInput(initial?.pickup_at, timezone));
  const [specification, setSpecification] = useState<Record<string, string>>(
    initial?.specification ?? initialProduct?.specification ?? {},
  );
  const [label, setLabel] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const changeProduct = (id: string) => {
    const next = products.find((item) => item.id === id);
    setProductId(id);
    setVariant(next?.variants[0] ?? '');
    setSizes(
      Object.fromEntries(
        (next?.sizes ?? []).map((size, index) => [size, index === 0 ? Number(quantity) : 0]),
      ),
    );
    setSpecification(next?.specification ?? {});
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setLocalError(null);
    try {
      if (
        product?.sizes.length &&
        Object.values(sizes).reduce((sum, count) => sum + count, 0) !== Number(quantity)
      )
        throw new Error('The size quantities must add up to the total quantity.');
      const pickup_at = pickupISO(pickup, timezone);
      onSubmit({
        ...(customer
          ? {
              customer_name: customerName.trim(),
              ...(email.trim() ? { customer_email: email.trim() } : {}),
            }
          : { ...(label.trim() ? { label: label.trim() } : {}) }),
        product_id: productId,
        quantity: Number(quantity),
        variant,
        sizes,
        pickup_at,
        specification,
      });
    } catch (e) {
      setLocalError(errorMessage(e));
    }
  };
  return (
    <form onSubmit={submit} className="form-stack">
      {customer && (
        <>
          <Field label="Customer name" htmlFor="customer-name">
            <input
              id="customer-name"
              required
              autoFocus
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              maxLength={140}
              placeholder="Name or organization"
            />
          </Field>
          <Field
            label="Customer email"
            htmlFor="customer-email"
            optional
            hint="Saved with the order. Sharing a proposal does not send an email."
          >
            <input
              id="customer-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="customer@example.com"
            />
          </Field>
          <div className="form-divider" />
        </>
      )}
      {!customer && (
        <Field label="Option name" htmlFor="proposal-label" optional>
          <input
            id="proposal-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="For example, keep the earlier pickup"
            maxLength={140}
          />
        </Field>
      )}
      <Field label="Product" htmlFor="order-product">
        <select
          id="order-product"
          value={productId}
          onChange={(e) => changeProduct(e.target.value)}
          required
        >
          {products.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </Field>
      <div className="form-row">
        <Field label="Quantity" htmlFor="order-quantity">
          <input
            id="order-quantity"
            type="number"
            min="1"
            max="10000"
            step="1"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </Field>
        <Field label={product?.profile === 'bakery' ? 'Flavor' : 'Color'} htmlFor="order-variant">
          <select
            id="order-variant"
            value={variant}
            onChange={(e) => setVariant(e.target.value)}
            required
          >
            {product?.variants.map((value) => (
              <option key={value} value={value}>
                {titleCase(value)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {Boolean(product?.sizes.length) && (
        <fieldset className="sizes-field">
          <legend>Size quantities</legend>
          <div className="sizes-inputs">
            {product?.sizes.map((size) => (
              <Field label={size} htmlFor={`size-${size}`} key={size}>
                <input
                  id={`size-${size}`}
                  type="number"
                  min="0"
                  max="10000"
                  step="1"
                  value={sizes[size] ?? 0}
                  onChange={(e) => setSizes({ ...sizes, [size]: Number(e.target.value) })}
                  required
                />
              </Field>
            ))}
          </div>
          <p className="field-hint">These must add up to {quantity || 'the total quantity'}.</p>
        </fieldset>
      )}
      <Field
        label="Pickup date and time"
        htmlFor="order-pickup"
        hint={`Business time: ${timezone.replaceAll('_', ' ')}.`}
      >
        <input
          id="order-pickup"
          type="datetime-local"
          value={pickup}
          onChange={(e) => setPickup(e.target.value)}
          required
        />
      </Field>
      {Object.keys(specification).length > 0 && (
        <fieldset className="spec-fields">
          <legend>Production specification</legend>
          {Object.entries(specification).map(([key, value]) => (
            <Field label={titleCase(key)} htmlFor={`spec-${key}`} key={key}>
              <input
                id={`spec-${key}`}
                value={value}
                onChange={(e) => setSpecification({ ...specification, [key]: e.target.value })}
                maxLength={500}
              />
            </Field>
          ))}
        </fieldset>
      )}
      {product && (
        <Notice title="The server calculates the quote.">
          <p>
            Saved rate: {money(product.unit_price_cents)} per unit. Configured rush fees, capacity,
            and the deposit rule are checked when the proposal is created.
          </p>
        </Notice>
      )}
      <ErrorNotice message={localError || error} />
      <div className="form-actions">
        {onCancel && (
          <Button type="button" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button variant="primary" type="submit" busy={busy}>
          {customer ? 'Create order & check terms' : 'Prepare this proposal'}{' '}
          <ArrowRight size={17} />
        </Button>
      </div>
    </form>
  );
}

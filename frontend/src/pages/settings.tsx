import { Check, Pencil, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useWorkspace } from '../lib/workspace';
import { ProductMark, Specification } from '../components/orders';
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Notice,
  PageIntro,
  Panel,
  PanelHeading,
} from '../components/ui';
import { useAuth } from '../lib/auth';
import { patch } from '../lib/api';
import { money, titleCase } from '../lib/format';
import { useAction, useApi } from '../lib/hooks';
import type { Product, ResourcesResponse, Terms, Workspace } from '../lib/types';

export function SettingsPage() {
  const workspace = useWorkspace();
  const auth = useAuth();
  const [name, setName] = useState(workspace.name);
  const [timezone, setTimezone] = useState(workspace.timezone);
  const [deposit, setDeposit] = useState(String(workspace.deposit_bps / 100));
  const [saved, setSaved] = useState(false);
  const [product, setProduct] = useState<Product | null>(null);
  const action = useAction();
  const resources = useApi<ResourcesResponse>(`/workspaces/${workspace.id}/resources`);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () =>
        patch<Workspace>(`/workspaces/${workspace.id}`, {
          name,
          timezone,
          deposit_bps: Math.round(Number(deposit) * 100),
        }),
      async () => {
        await auth.refresh();
        setSaved(true);
      },
    );
  };
  return (
    <>
      <PageIntro eyebrow="Business setup" title="Your business. Your operating rules.">
        <p>The same workflow uses your products, required details, pricing, and resource limits.</p>
      </PageIntro>
      <div className="settings-grid">
        <Panel>
          <PanelHeading title="Business details">
            <Badge>{titleCase(workspace.profile)}</Badge>
          </PanelHeading>
          <form onSubmit={submit} className="form-stack">
            <Field label="Business name" htmlFor="settings-name">
              <input
                id="settings-name"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setSaved(false);
                }}
                required
                maxLength={120}
              />
            </Field>
            <Field
              label="Business timezone"
              htmlFor="settings-timezone"
              hint="New pickup commitments use this timezone. Accepted terms retain their saved context."
            >
              <input
                id="settings-timezone"
                value={timezone}
                onChange={(e) => {
                  setTimezone(e.target.value);
                  setSaved(false);
                }}
                required
              />
            </Field>
            <div className="form-row">
              <Field label="Required deposit" htmlFor="settings-deposit">
                <div className="input-affix">
                  <input
                    id="settings-deposit"
                    type="number"
                    min="0"
                    max="100"
                    step="1"
                    value={deposit}
                    onChange={(e) => {
                      setDeposit(e.target.value);
                      setSaved(false);
                    }}
                    required
                  />
                  <span>%</span>
                </div>
              </Field>
              <Field label="Currency" htmlFor="settings-currency">
                <input id="settings-currency" value={workspace.currency} disabled />
              </Field>
            </div>
            <ErrorNotice message={action.error} />
            <div className="form-actions">
              {saved && (
                <span className="saved-note" role="status">
                  <Check size={16} /> Settings saved
                </span>
              )}
              <Button type="submit" variant="primary" busy={action.pending}>
                Save business details
              </Button>
            </div>
          </form>
        </Panel>
        <Panel>
          <PanelHeading title="How commitments are protected">
            <ShieldCheck size={18} />
          </PanelHeading>
          {[
            {
              title: 'Requests are checked against the current agreement.',
              text: 'Source messages stay attached to the order. Unknown details remain unresolved.',
            },
            {
              title: 'Prices come from the saved rate card.',
              text: 'The server calculates totals, rush fees, deposit requirements, and feasibility.',
            },
            {
              title: 'Customer consent applies to a specific revision.',
              text: 'Resource availability is checked again before that revision becomes the commitment.',
            },
            {
              title: 'Production has explicit release conditions.',
              text: 'The accepted revision, resources, required deposit, and any hold are checked together.',
            },
          ].map((item) => (
            <div className="setup-rule" key={item.title}>
              <Check size={17} />
              <div>
                <strong>{item.title}</strong>
                <p>{item.text}</p>
              </div>
            </div>
          ))}
        </Panel>
        <Panel className="settings-wide">
          <PanelHeading title="Products & rate card">
            <Badge>Server-priced terms</Badge>
          </PanelHeading>
          <p className="settings-description">
            Review these rules before quoting real orders. Changes affect new proposals; previously
            accepted revisions keep their agreed terms.
          </p>
          <ErrorNotice message={resources.error} retry={() => void resources.refresh()} />
          {resources.loading ? (
            <Loading compact label="Loading the rate card…" />
          ) : (
            resources.data?.products.map((item) => (
              <div key={item.id} className="rate-card">
                <ProductMark profile={item.profile} small />
                <div className="rate-main">
                  <h3>{item.name}</h3>
                  <p>
                    {item.variants.map(titleCase).join(' · ')}
                    {item.sizes.length ? ` · Sizes ${item.sizes.join(', ')}` : ''}
                  </p>
                  <div className="rate-values">
                    <span>
                      <strong>{money(item.unit_price_cents, workspace.currency)}</strong> per unit
                    </span>
                    <span>
                      <strong>{money(item.rush_fee_cents, workspace.currency)}</strong> rush fee
                    </span>
                    <span>
                      <strong>{item.capacity_units_per_item}</strong>{' '}
                      {workspace.profile === 'bakery' ? 'preparation minutes' : 'capacity units'} /
                      item
                    </span>
                  </div>
                  <Specification terms={{ specification: item.specification } as Terms} />
                </div>
                <Button onClick={() => setProduct(item)}>
                  <Pencil size={14} /> Edit rules
                </Button>
              </div>
            ))
          )}
        </Panel>
      </div>
      {product && (
        <EditProduct
          product={product}
          workspaceId={workspace.id}
          onClose={() => setProduct(null)}
          onSaved={async () => {
            setProduct(null);
            await resources.refresh();
          }}
        />
      )}
    </>
  );
}

function EditProduct({
  product,
  workspaceId,
  onClose,
  onSaved,
}: {
  product: Product;
  workspaceId: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(product.name);
  const [price, setPrice] = useState((product.unit_price_cents / 100).toFixed(2));
  const [rush, setRush] = useState((product.rush_fee_cents / 100).toFixed(2));
  const [units, setUnits] = useState(String(product.capacity_units_per_item));
  const [leadDays, setLeadDays] = useState(String(product.lead_days ?? 7));
  const [specification, setSpecification] = useState(product.specification);
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () =>
        patch<Product>(`/workspaces/${workspaceId}/products/${product.id}`, {
          name,
          unit_price_cents: Math.round(Number(price) * 100),
          rush_fee_cents: Math.round(Number(rush) * 100),
          capacity_units_per_item: Number(units),
          lead_days: Number(leadDays),
          specification,
        }),
      onSaved,
    );
  };
  return (
    <Modal title="Edit product rules" onClose={onClose}>
      <form className="modal-body form-stack" onSubmit={submit}>
        <Notice title="Existing agreements keep their terms.">
          These changes apply when the server calculates a new proposal.
        </Notice>
        <Field label="Product name" htmlFor="product-name">
          <input
            id="product-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </Field>
        <div className="form-row">
          <Field label="Unit price" htmlFor="product-price">
            <input
              id="product-price"
              type="number"
              min="0.01"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              required
            />
          </Field>
          <Field label="Rush fee" htmlFor="product-rush">
            <input
              id="product-rush"
              type="number"
              min="0"
              step="0.01"
              value={rush}
              onChange={(e) => setRush(e.target.value)}
              required
            />
          </Field>
        </div>
        <div className="form-row">
          <Field
            label={
              product.profile === 'bakery'
                ? 'Preparation minutes per item'
                : 'Capacity units per item'
            }
            htmlFor="product-units"
          >
            <input
              id="product-units"
              type="number"
              min="1"
              step="1"
              value={units}
              onChange={(e) => setUnits(e.target.value)}
              required
            />
          </Field>
          <Field label="Standard lead time (days)" htmlFor="product-lead">
            <input
              id="product-lead"
              type="number"
              min="0"
              step="1"
              value={leadDays}
              onChange={(e) => setLeadDays(e.target.value)}
              required
            />
          </Field>
        </div>
        {Object.entries(specification).map(([key, value]) => (
          <Field label={titleCase(key)} key={key} htmlFor={`product-spec-${key}`}>
            <input
              id={`product-spec-${key}`}
              value={value}
              onChange={(e) => setSpecification({ ...specification, [key]: e.target.value })}
            />
          </Field>
        ))}
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending}>
            Save product rules
          </Button>
        </div>
      </form>
    </Modal>
  );
}

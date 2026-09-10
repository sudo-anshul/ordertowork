import { CalendarDays, Package, Plus } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useWorkspace } from '../lib/workspace';
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Notice,
  PageIntro,
  Panel,
} from '../components/ui';
import { patch, post } from '../lib/api';
import { useAction, useApi } from '../lib/hooks';
import { titleCase } from '../lib/format';
import type { Product, Resource, ResourcesResponse } from '../lib/types';

export function ResourcesPage() {
  const workspace = useWorkspace();
  const path = `/workspaces/${workspace.id}/resources`;
  const { data, loading, error, refresh } = useApi<ResourcesResponse>(path);
  const [editing, setEditing] = useState<Resource | null>(null);
  const [adding, setAdding] = useState(false);
  return (
    <>
      <PageIntro
        eyebrow="Resource availability"
        title="Make promises the business can keep."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus size={16} /> Add stock or capacity
          </Button>
        }
      >
        <p>
          Set the total resources your business can offer. Reserved quantities belong to existing
          commitments and cannot be removed.
        </p>
      </PageIntro>
      {workspace.is_demo && (
        <Notice tone="warning" title="This workspace contains sample resources.">
          These values demonstrate the workflow. Configure resources in a separate real workspace
          before handling customer orders.
        </Notice>
      )}
      <ErrorNotice message={error} retry={() => void refresh()} />
      {loading ? (
        <Loading label="Loading stock and capacity…" />
      ) : (
        <div className="resource-sections">
          {(['stock', 'capacity'] as const).map((kind) => {
            const items = data?.resources.filter((resource) => resource.kind === kind) ?? [];
            return (
              <section key={kind}>
                <div className="resource-heading">
                  <div>
                    <h2>{kind === 'stock' ? 'Materials & stock' : 'Production capacity'}</h2>
                    <p>
                      {kind === 'stock'
                        ? 'Specific items available for customer orders.'
                        : `Capacity dates follow ${workspace.timezone.replaceAll('_', ' ')}.`}
                    </p>
                  </div>
                  <Badge>
                    {items.length} {items.length === 1 ? 'resource' : 'resources'}
                  </Badge>
                </div>
                {items.length ? (
                  <Panel>
                    <div className="table-overflow">
                      <table className="resource-table">
                        <thead>
                          <tr>
                            <th>Resource</th>
                            <th>Total</th>
                            <th>Reserved</th>
                            <th>Available</th>
                            <th>
                              <span className="sr-only">Actions</span>
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {items.map((resource) => (
                            <tr key={resource.id}>
                              <td>
                                <strong>{resource.label}</strong>
                                <small>
                                  {titleCase(resource.kind)} · {resource.unit}
                                </small>
                              </td>
                              <td className="numeric">{resource.total}</td>
                              <td className="numeric">
                                {resource.reserved}
                                <div className="capacity-meter" aria-hidden="true">
                                  <span
                                    style={{
                                      width: `${resource.total ? Math.min(100, (resource.reserved / resource.total) * 100) : 0}%`,
                                    }}
                                  />
                                </div>
                              </td>
                              <td className="numeric">
                                <Badge tone={resource.available > 0 ? 'green' : 'amber'}>
                                  {resource.available} {resource.unit}
                                </Badge>
                              </td>
                              <td>
                                <Button onClick={() => setEditing(resource)}>Edit total</Button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Panel>
                ) : (
                  <EmptyState
                    icon={kind === 'stock' ? <Package size={28} /> : <CalendarDays size={28} />}
                    title={
                      kind === 'stock'
                        ? 'No stock resources configured.'
                        : 'Add your available production dates.'
                    }
                    action={
                      <Button onClick={() => setAdding(true)}>
                        <Plus size={16} /> Add resource
                      </Button>
                    }
                  >
                    {kind === 'stock'
                      ? 'Only configured materials are checked. Bakery preparation may be controlled through capacity alone.'
                      : 'The agent needs configured slots before it can make a reliable pickup promise.'}
                  </EmptyState>
                )}
              </section>
            );
          })}
        </div>
      )}
      {editing && (
        <EditResource
          resource={editing}
          path={path}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}
      {adding && (
        <AddResource
          path={path}
          profile={workspace.profile}
          products={data?.products ?? []}
          onClose={() => setAdding(false)}
          onSaved={async () => {
            setAdding(false);
            await refresh();
          }}
        />
      )}
    </>
  );
}

function EditResource({
  resource,
  path,
  onClose,
  onSaved,
}: {
  resource: Resource;
  path: string;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [total, setTotal] = useState(String(resource.total));
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () => patch<Resource>(`${path}/${resource.id}`, { total: Number(total) }),
      onSaved,
    );
  };
  return (
    <Modal title="Update resource total" onClose={onClose}>
      <form onSubmit={submit} className="modal-body form-stack">
        <p>{resource.label}</p>
        <Field
          label={`Total ${resource.unit}`}
          htmlFor="resource-total"
          hint={`${resource.reserved} ${resource.unit} are already reserved. You cannot reduce the total below that commitment.`}
        >
          <input
            id="resource-total"
            type="number"
            min={resource.reserved}
            step="1"
            value={total}
            onChange={(e) => setTotal(e.target.value)}
            required
            autoFocus
          />
        </Field>
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending}>
            Save total
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function AddResource({
  path,
  profile,
  products,
  onClose,
  onSaved,
}: {
  path: string;
  profile: string;
  products: Product[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [kind, setKind] = useState<'capacity' | 'stock'>('capacity');
  const [date, setDate] = useState('');
  const [label, setLabel] = useState('');
  const [total, setTotal] = useState('');
  const [productId, setProductId] = useState(products[0]?.id ?? '');
  const product = products.find((item) => item.id === productId);
  const [variant, setVariant] = useState(products[0]?.variants[0] ?? '');
  const [size, setSize] = useState(products[0]?.sizes[0] ?? '');
  const unit = kind === 'capacity' ? (profile === 'bakery' ? 'minutes' : 'shirts') : 'units';
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const payload =
      kind === 'capacity'
        ? {
            kind,
            key: `capacity:${date}`,
            label: label.trim() || `${date} production capacity`,
            unit,
            total: Number(total),
            metadata: { date },
          }
        : {
            kind,
            key: `stock:${productId}:${variant}:${size}`,
            label: label.trim() || `${product?.name} · ${titleCase(variant)} · ${size}`,
            unit,
            total: Number(total),
            metadata: { product_id: productId, variant, size },
          };
    void action.run(() => post<Resource>(path, payload), onSaved);
  };
  return (
    <Modal title="Add a resource" onClose={onClose}>
      <form onSubmit={submit} className="modal-body form-stack">
        <Field label="Resource type" htmlFor="resource-kind">
          <select
            id="resource-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as 'stock' | 'capacity')}
          >
            <option value="capacity">Production capacity</option>
            {profile === 'merchandise' && <option value="stock">Product stock</option>}
          </select>
        </Field>
        {kind === 'capacity' ? (
          <Field label="Production date" htmlFor="capacity-date">
            <input
              type="date"
              id="capacity-date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
            />
          </Field>
        ) : (
          <>
            <Field label="Product" htmlFor="stock-product">
              <select
                id="stock-product"
                value={productId}
                onChange={(e) => {
                  const next = products.find((item) => item.id === e.target.value);
                  setProductId(e.target.value);
                  setVariant(next?.variants[0] ?? '');
                  setSize(next?.sizes[0] ?? '');
                }}
              >
                {products.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <div className="form-row">
              <Field label="Color" htmlFor="stock-variant">
                <select
                  id="stock-variant"
                  value={variant}
                  onChange={(e) => setVariant(e.target.value)}
                >
                  {product?.variants.map((item) => (
                    <option key={item} value={item}>
                      {titleCase(item)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Size" htmlFor="stock-size">
                <select
                  id="stock-size"
                  value={size}
                  onChange={(e) => setSize(e.target.value)}
                  required
                >
                  {product?.sizes.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </>
        )}
        <Field label="Resource label" htmlFor="new-resource-label" optional>
          <input
            id="new-resource-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={kind === 'capacity' ? 'Friday preparation slot' : 'Navy medium blanks'}
            maxLength={150}
          />
        </Field>
        <Field label={`Total ${unit}`} htmlFor="new-resource-total">
          <input
            id="new-resource-total"
            type="number"
            min="1"
            step="1"
            required
            value={total}
            onChange={(e) => setTotal(e.target.value)}
          />
        </Field>
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending}>
            Add resource
          </Button>
        </div>
      </form>
    </Modal>
  );
}

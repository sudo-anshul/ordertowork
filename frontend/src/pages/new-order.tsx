import { ArrowLeft } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../lib/workspace';
import { OrderForm } from '../components/order-form';
import { EmptyState, ErrorNotice, Loading, PageIntro, Panel } from '../components/ui';
import { post } from '../lib/api';
import { useAction, useApi } from '../lib/hooks';
import type { OrderDetail, ResourcesResponse } from '../lib/types';

export function NewOrderPage() {
  const workspace = useWorkspace();
  const { data, error, loading, refresh } = useApi<ResourcesResponse>(
    `/workspaces/${workspace.id}/resources`,
  );
  const action = useAction();
  const navigate = useNavigate();
  if (workspace.role !== 'owner')
    return (
      <EmptyState title="An owner creates new orders.">
        You can find released work in the production queue.
      </EmptyState>
    );
  return (
    <>
      <PageIntro
        eyebrow="A new agreement"
        title="Start with the details you know."
        actions={
          <Link className="button button-secondary" to={`/w/${workspace.id}/orders`}>
            <ArrowLeft size={16} /> Back to orders
          </Link>
        }
      >
        <p>
          Create a proposal from the saved product rules. The customer will review the exact terms
          before anything is confirmed.
        </p>
      </PageIntro>
      <ErrorNotice message={error} retry={() => void refresh()} />
      {loading ? (
        <Loading label="Loading your products…" />
      ) : data?.products.length ? (
        <div className="editor-grid">
          <Panel className="panel-padded">
            <OrderForm
              products={data.products}
              timezone={workspace.timezone}
              customer
              busy={action.pending}
              error={action.error}
              onSubmit={(input) =>
                void action.run(
                  () => post<OrderDetail>(`/workspaces/${workspace.id}/orders`, input),
                  (order) => navigate(`/w/${workspace.id}/orders/${order.id}`),
                )
              }
            />
          </Panel>
          <aside className="editor-aside">
            <p className="eyebrow">From request to ready</p>
            <h2>Keep the commitment clear.</h2>
            <p>
              Creating an order prepares a quote. It does not record customer approval, take
              payment, or release production.
            </p>
            <ol>
              <li>Review the calculated proposal.</li>
              <li>Share a link with the customer.</li>
              <li>Let the customer approve the exact revision.</li>
              <li>Record the received deposit, then release the work.</li>
            </ol>
          </aside>
        </div>
      ) : (
        <EmptyState title="A product is needed first.">
          This workspace has no product rate card. Ask the workspace administrator to configure
          products before creating an order.
        </EmptyState>
      )}
    </>
  );
}

import { api } from './api';
import type { OrderSummary, Session } from './types';

export async function sampleDestination(session: Session): Promise<string> {
  const workspace =
    session.workspaces.find((item) => item.profile === 'merchandise') ?? session.workspaces[0];
  if (!workspace) return '/setup';
  const base = `/w/${workspace.id}`;
  try {
    const { orders } = await api<{ orders: OrderSummary[] }>(`/workspaces/${workspace.id}/orders`);
    const sample = orders.find((order) => order.is_demo);
    if (sample) return `${base}/orders/${sample.id}`;
  } catch {
    // A temporary order-list error must not discard a valid session.
  }
  return `${base}/decisions`;
}

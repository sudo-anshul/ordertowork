import { ArrowLeft } from 'lucide-react';
import { Link, Navigate, Route, Routes } from 'react-router-dom';
import {
  AppLayout,
  BusinessAccountGuard,
  Home,
  OperationsLayout,
  OwnerGuard,
  SessionGuard,
} from './components/layout';
import { useWorkspace } from './lib/workspace';
import { EmptyState } from './components/ui';
import { LoginPage, SetupPage } from './pages/auth';
import { CustomerPage } from './pages/customer';
import { NewOrderPage } from './pages/new-order';
import { OrderDetailPage } from './pages/order-detail';
import { DecisionsPage, OrdersPage } from './pages/orders-list';
import { ResourcesPage } from './pages/resources';
import { SettingsPage } from './pages/settings';
import { TeamPage } from './pages/team';
import { OperationsPage } from './pages/operations';
import { ProductionQueuePage, ProductionTicketPage } from './pages/production';

function WorkspaceHome() {
  const workspace = useWorkspace();
  return <Navigate to={workspace.role === 'operator' ? 'production' : 'decisions'} replace />;
}
export default function App() {
  return (
    <Routes>
      <Route path="/customer/:token" element={<CustomerPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<Home />} />
      <Route element={<SessionGuard />}>
        <Route element={<BusinessAccountGuard />}>
          <Route path="/setup" element={<SetupPage />} />
        </Route>
        <Route path="/w/:workspaceId" element={<AppLayout />}>
          <Route index element={<WorkspaceHome />} />
          <Route path="production" element={<ProductionQueuePage />} />
          <Route path="production/:orderId" element={<ProductionTicketPage />} />
          <Route element={<OwnerGuard />}>
            <Route path="decisions" element={<DecisionsPage />} />
            <Route path="orders" element={<OrdersPage />} />
            <Route path="orders/:orderId" element={<OrderDetailPage />} />
            <Route path="resources" element={<ResourcesPage />} />
            <Route element={<BusinessAccountGuard />}>
              <Route path="orders/new" element={<NewOrderPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="team" element={<TeamPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="/operations" element={<OperationsLayout />}>
          <Route index element={<OperationsPage />} />
        </Route>
      </Route>
      <Route
        path="*"
        element={
          <div className="standalone">
            <EmptyState
              title="This page isn’t here."
              action={
                <Link className="button button-primary" to="/">
                  <ArrowLeft size={16} /> Back to your workspace
                </Link>
              }
            >
              The link may have changed. Your orders are still in your workspace.
            </EmptyState>
          </div>
        }
      />
    </Routes>
  );
}

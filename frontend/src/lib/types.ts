export type WorkspaceRole = 'owner' | 'operator';
export interface Workspace {
  id: string;
  name: string;
  profile: 'merchandise' | 'bakery';
  currency: string;
  timezone: string;
  deposit_bps: number;
  role: WorkspaceRole;
  status: string;
  is_demo?: boolean;
}
export interface User {
  id: string;
  email: string;
  name: string;
  email_verified: boolean;
  is_platform_admin: boolean;
}
export interface Session {
  user: User;
  workspaces: Workspace[];
  csrf_token: string;
  auth_method: 'demo' | 'reviewer' | 'cognito' | 'development';
  reviewer: { expires_at: string } | null;
  demo: {
    expires_at: string;
    max_agent_jobs_per_workspace: number;
  } | null;
}
export interface AuthConfig {
  auth_mode: 'development' | 'cognito';
  development_login_enabled: boolean;
  demo_enabled: boolean;
  login_url: string | null;
  configured: boolean;
}
export interface Issue {
  code: string;
  message: string;
  resource_id?: string;
  required?: number;
  available?: number;
}
export interface Terms {
  product_id: string;
  product_name: string;
  quantity: number;
  variant: string;
  sizes: Record<string, number>;
  pickup_at: string;
  unit_price_cents: number;
  subtotal_cents: number;
  rush_fee_cents: number;
  total_cents: number;
  required_deposit_cents: number;
  currency: string;
  specification: Record<string, string>;
}
export interface Revision {
  id: string;
  number: number;
  status: string;
  label: string;
  terms: Terms;
  terms_hash: string;
  feasible: boolean;
  issues: Issue[];
  created_at: string;
}
export type OrderStatus =
  | 'awaiting_approval'
  | 'needs_review'
  | 'ready'
  | 'deposit_due'
  | 'on_hold'
  | 'in_production'
  | 'ready_for_handover'
  | 'awaiting_collection'
  | 'awaiting_dispatch'
  | 'out_for_delivery'
  | 'completed'
  | 'new';
export type ProductionStatus = 'not_started' | 'started' | 'finished';
export interface OrderSummary {
  id: string;
  number: string;
  customer_name: string;
  customer_email: string | null;
  is_demo: boolean;
  status: OrderStatus;
  production_status: ProductionStatus;
  handover_status: HandoverStatus | null;
  hold_reason: string | null;
  accepted_revision: Revision | null;
  deposit_paid_cents: number;
  created_at: string;
}
export interface Message {
  id: string;
  body: string;
  source: string;
  created_at: string;
}
export interface OrderEvent {
  id: string;
  type: string;
  message: string;
  created_at: string;
}
export interface Reservation {
  resource_id: string;
  label: string;
  quantity: number;
  unit: string;
}
export interface Analysis {
  intent: string;
  evidence: string | Record<string, unknown> | unknown[];
  missing_fields: string[];
  requested_issues: Issue[];
}
export interface AnalysisJob {
  id: string;
  status: string;
  error?: string | null;
  error_message?: string | null;
  mode?: string;
  attempts?: number;
  created_at?: string;
  updated_at?: string;
  tool_events?: { tool: string; input?: Record<string, unknown>; result?: unknown }[];
}
export interface OrderDetail extends OrderSummary {
  handover: Handover | null;
  revisions: Revision[];
  messages: Message[];
  events: OrderEvent[];
  reservations: Reservation[];
  production_ready: boolean;
  production_blockers: string[];
  shared_revision_id: string | null;
  latest_analysis: Analysis | null;
  latest_job?: AnalysisJob | null;
}
export interface Resource {
  id: string;
  key: string;
  kind: 'stock' | 'capacity';
  label: string;
  unit: string;
  total: number;
  reserved: number;
  available: number;
  metadata: Record<string, string | number>;
}
export interface Product {
  id: string;
  name: string;
  profile: 'merchandise' | 'bakery';
  unit_price_cents: number;
  rush_fee_cents: number;
  capacity_units_per_item: number;
  variants: string[];
  sizes: string[];
  specification: Record<string, string>;
  lead_days?: number;
}
export interface ResourcesResponse {
  resources: Resource[];
  products: Product[];
}
export interface ShareResult {
  url: string;
  expires_at: string;
  revision_id: string;
  terms_hash: string;
}
export interface CustomerOrder {
  business: { name: string; currency: string; timezone: string };
  order: { number: string; customer_name: string; is_demo: boolean };
  revision: Revision;
  previous_terms: Terms | null;
  deposit_paid_cents: number;
  top_up_cents: number;
  balance_after_deposit_cents: number;
  expires_at: string;
  status: 'pending' | 'approved';
  approval_mode: 'bearer_link';
}
export interface Ticket {
  number: string;
  customer_name: string;
  revision: number | Revision;
  terms: Terms;
  deposit_paid_cents: number;
  balance_cents: number;
  reservations: Reservation[];
  is_demo: boolean;
  production_status: ProductionStatus;
}
export interface Member {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: WorkspaceRole;
  created_at: string;
}
export type OperationalTerms = Omit<
  Terms,
  | 'unit_price_cents'
  | 'subtotal_cents'
  | 'rush_fee_cents'
  | 'total_cents'
  | 'required_deposit_cents'
  | 'currency'
>;
export interface OperationalTicket {
  number: string;
  customer_name: string;
  revision: number | Revision;
  terms: OperationalTerms;
  reservations: Reservation[];
  is_demo: boolean;
  production_status: ProductionStatus;
}
export interface ProductionOrder {
  id: string;
  number: string;
  customer_name: string;
  is_demo: boolean;
  production_status: ProductionStatus;
  revision: number;
  pickup_at: string;
  product_name: string;
  quantity: number;
  variant: string;
  sizes: Record<string, number>;
  specification: Record<string, string>;
}

export type HandoverStatus =
  | 'awaiting_choice'
  | 'delivery_requested'
  | 'quote_ready'
  | 'confirmed'
  | 'out_for_delivery'
  | 'collected'
  | 'delivered';
export type DeliveryMode = 'unavailable' | 'included' | 'fixed' | 'quote';
export interface HandoverDefaults {
  collection_address: string;
  collection_instructions: string;
  delivery_mode: DeliveryMode;
  delivery_fee_cents: number;
  delivery_area: string;
}
export interface HandoverPricing {
  order_total_cents: number;
  delivery_fee_cents: number | null;
  total_cents: number | null;
  paid_cents: number;
  balance_cents: number | null;
  currency: string;
}
export interface Handover {
  id: string;
  version: number;
  status: HandoverStatus;
  revision: number;
  config: HandoverDefaults & {
    timezone?: string;
    collection_window_start: string;
    collection_window_end: string;
  };
  method: 'collection' | 'delivery' | null;
  collection_at: string | null;
  delivery_address: string | null;
  contact_phone: string | null;
  customer_note: string | null;
  delivery_fee_cents: number | null;
  quote_note: string | null;
  quote_hash: string | null;
  pricing: HandoverPricing;
  on_hold: boolean;
  ready_at: string;
  confirmed_at: string | null;
  dispatched_at: string | null;
  completed_at: string | null;
  link_active?: boolean;
  link_expires_at?: string | null;
}
export interface HandoverResponse {
  handover: Handover | null;
  defaults: HandoverDefaults;
}
export interface HandoverShare {
  url: string;
  expires_at: string;
  notification_text: string;
}
export interface CustomerHandover {
  business: { name: string; currency: string; timezone: string };
  order: {
    number: string;
    customer_name: string;
    is_demo: boolean;
    revision: number;
    product_name: string;
    quantity: number;
    variant: string;
  };
  handover: Handover;
  expires_at: string;
}

# OrderToWork HTTP contract

All application routes below are prefixed `/api`. Monetary fields are integer cents. ISO datetimes include an offset; the workspace timezone is used for capacity dates. Business routes authenticate via cookie and all mutations require `X-CSRF-Token`. Error `detail` is `{code,message}`. IDs are opaque UUID strings. Public customer links are bearer credentials: never log/store full URLs in analytics.

## Core domain objects

`Terms = {product_id, product_name, quantity, variant, sizes: Record<string,number>, pickup_at, unit_price_cents, subtotal_cents, rush_fee_cents, total_cents, required_deposit_cents, currency, specification: Record<string,string>}`. The backend owns all pricing. Merchandise `variant` is `navy`/`charcoal`, sizes `S,M,L`; bakery `variant` is `vanilla`, sizes `{}`, specification includes `icing: Blue` and `recipe: V1`.

`OrderSummary = {id,number,customer_name,customer_email,is_demo,status,production_status,hold_reason,accepted_revision: Revision|null,deposit_paid_cents,created_at}`.

`Revision = {id,number,base_accepted_revision_id,status,label,terms,terms_hash,feasible,issues:[{code,message,resource_id?,required?,available?}],created_at}`.

`OrderDetail` extends `OrderSummary` with `{latest_job:{id,status,error}|null,revisions:Revision[],messages:[{id,body,source,created_at}],events:[{id,type,message,created_at}],reservations:[{resource_id,label,quantity,unit}],production_ready:boolean,production_blockers:string[],shared_revision_id:string|null,latest_analysis: {intent,evidence,missing_fields,requested_issues}|null}`. A proposed revision never changes the accepted revision. Exact fresh availability is rechecked at customer approval. Revision hashes include the prior accepted revision ID, exact terms and frozen resource requirements. A revision cannot be shared or approved against a different current agreement; the canonical hash is recomputed before approval.

`Resource = {id,key,kind:stock|capacity,label,unit,total,reserved,available,metadata}`.

`Product = {id,name,profile,unit_price_cents,rush_fee_cents,capacity_units_per_item,variants:string[],sizes:string[],specification}`.

## Workspace routes

- `GET /workspaces/{wid}/orders` owner → `{orders: OrderSummary[]}`.
- `POST /workspaces/{wid}/orders` owner body `{customer_name,customer_email?,product_id,quantity,variant,sizes?,pickup_at,specification?}` → `OrderDetail` (201). A new order has no accepted revision and its initial quote must be explicitly shared/approved. Max quantity 10000.
- `GET /workspaces/{wid}/orders/{oid}` owner → `OrderDetail`.
- `POST /workspaces/{wid}/orders/{oid}/messages` owner body `{body,source?:"manual"}` → `{message:{id,body,source,created_at},job:{id,status}}` (202). Saves source and queues analysis; root-owned job API supplies status polling.
- `POST /workspaces/{wid}/orders/{oid}/proposals` owner body `{product_id?,quantity,variant,sizes?,pickup_at,specification?,label?}` → `OrderDetail`. Explicit owner-authored proposal, always server-priced. Infeasible proposals remain visible but cannot be shared.
- `POST /workspaces/{wid}/orders/{oid}/proposals/{rid}/share` owner body `{}` → `{url,expires_at,revision_id,terms_hash}`. Returns plaintext link only at creation, expiring in 72h; replacement share invalidates previous links. Owner copies link; no external message is sent.
- `POST /workspaces/{wid}/orders/{oid}/deposits` owner body `{amount_cents,reference,idempotency_key}` → `OrderDetail`. Manually recorded payment, never an actual payment capture. Both amount and key positive/nonempty; same key with different body is 409.
- `POST /workspaces/{wid}/orders/{oid}/hold` owner body `{reason:string|null}` → `OrderDetail`. Explicit production hold, `null` clears.
- `GET /workspaces/{wid}/orders/{oid}/ticket` → `{number,customer_name,revision,terms,deposit_paid_cents,balance_cents,reservations,is_demo,production_status}`. 409 until accepted current revision, reserved resources, sufficient deposit and no hold. Owner/operator; operators receive the stripped `OperationalTicket` below.
- `POST /workspaces/{wid}/orders/{oid}/production/start` owner/operator body `{expected_revision:int}` → `OrderDetail` for owner, `OperationalTicket` for operator. Requires ticket release gates and the exact current accepted revision number displayed on the reviewed ticket. Returns 409 `stale_revision` if the agreement changed; the client must reload and review before retrying. Repeating a start for the same already-started revision is idempotent. Cannot accept further changes automatically once started.
- `GET /workspaces/{wid}/resources` owner → `{resources:Resource[],products:Product[]}`.
- `PATCH /workspaces/{wid}/resources/{resource_id}` owner body `{total:number}` → `Resource`. Cannot reduce below existing reservations; positive integers only.
- `POST /workspaces/{wid}/resources` owner body `{kind,key,label,unit,total,metadata}` → `Resource` (201). Capacity key `capacity:YYYY-MM-DD`, metadata `{date:"YYYY-MM-DD"}`. Stock key `stock:{product_id}:{variant}:{size}`.

## Public customer routes

- `GET /customer/{token}` → `{business:{name,currency,timezone},order:{number,customer_name,is_demo},revision:Revision,previous_terms:Terms|null,deposit_paid_cents,top_up_cents,balance_after_deposit_cents,expires_at,status:"pending"|"approved",approval_mode:"bearer_link"}`. Only selected terms, no internal availability/events/customer email/team. Invalid, revoked/expired/stale links are 410; approved link permits viewing receipt while it remains accepted revision.
- `POST /customer/{token}/approve` body `{terms_hash,consent:true}` → `{status:"approved"|"already_approved",revision_id,order_number,required_deposit_cents,top_up_cents}`. Link/hash/revision bound, atomic reservation change. Availability failure 409 `availability_changed` leaves previous order and reservations untouched. No unchecked submission.
- `POST /customer/{token}/request-change` body `{body}` → `{status:"change_requested"}`. Revokes link, records customer source and owner action; preserves accepted order and reservations.

## Agent service integration (Python)

`analyze_change(db:Session, workspace_id:str, order_id:str, request:dict) -> dict` accepts normalized `{quantity,variant,sizes,pickup_at,intent,evidence,missing_fields,specification?}`. Optional `product_id`; unknown values or missing required fields are surfaced. Supported intents `change_request`, `new_order`, `question`, `approval`, `cancellation`, `unknown`. Agent-extracted `approval` never accepts terms. Returns order detail; caller commits. `order_snapshot(db,wid,oid)` returns trusted detail plus product/resources for tool use. Service actor/tenant IDs must come from trusted job metadata, never model output. `record_message` returns persisted Message; API then calls `enqueue_analysis(db,workspace_id,order_id,message_id)` and commits once.

## Sample profiles

Demo workspace seeds one originally accepted order and its source change message, resources, reservations, recorded sample deposit. Explicit `is_demo` on orders and sample tickets. Merchandise: 30 navy shirts S6/M12/L12 $540; requested45/Thursday is infeasible. Options45 charcoal Friday $810/top-up135 and30 navy Thursday $630/top-up45. Bakery:24 cupcakes $96; requested36 Friday is infeasible. Options36 Saturday $144/top-up24 and24 Friday $116/top-up10. Real new orders have no automatic acceptance or sample deposit.

Order.status values: `new`, `needs_review`, `awaiting_approval`, `deposit_due`, `ready`, `on_hold`, `in_production`. production_status is `not_started` or `started`. Production readiness is independent of pending exploratory suggestions unless an owner explicitly sets a hold.

Owner product settings: `PATCH /workspaces/{wid}/products/{pid}` body partial `{name,unit_price_cents,rush_fee_cents,capacity_units_per_item,lead_days,specification}` → Product. Existing revisions retain frozen prices/deposit policy/timezone and resource requirements. Real workspaces start resource totals at zero; the owner must configure actual availability. Catalogue prices are editable starter templates, not verified business facts. Real workspaces start with empty specifications; sample proof/recipe references exist only in explicitly seeded demo workspaces.

## Production operator access

Operators cannot read `/orders`, `/orders/{oid}`, `/resources`, commercial job traces, or source files. They cannot share proposals, record deposits, change rules, or edit resources. Frontends should route them directly to the production queue.

- `GET /workspaces/{wid}/production` owner/operator → `{orders: ProductionSummary[]}`. Includes only accepted orders satisfying all production-release gates (including those already started).
- `ProductionSummary = {id,number,customer_name,is_demo,production_status,revision,pickup_at,product_name,quantity,variant,sizes,specification}`.
- `OperationalTicket = {number,customer_name,revision,terms:{product_id,product_name,quantity,variant,sizes,pickup_at,timezone,specification},reservations,is_demo,production_status}`.
- Operator `GET /orders/{oid}/ticket` and `POST /orders/{oid}/production/start` return the same operational shape. Missing financial fields are intentionally absent and must never be displayed as zero. Owner responses retain the full existing contract.

Release gates are server enforced for both roles. Starting production commits held resources and invalidates pending customer links. Later requests require owner resolution; automatic changes cannot undo started work.

## Reference interpreter limits

`reference` is a deterministic development harness, not an AI model. It accepts simple quantity/variant/dated pickup changes and the documented synthetic examples. Negations, multiple totals or size deltas, conflicting variants or dates, unsupported relative date qualifiers, invalid times and changed recipe/artwork/dietary specifications are returned for owner clarification. It never converts ordinary message approval into customer acceptance. Unsupported fields must not silently disappear from a prepared option. The separately configured Bedrock/Strands path remains untested against a live model until AWS access and budget are available.

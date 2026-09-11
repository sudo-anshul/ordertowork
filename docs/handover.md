# Ready for handover

The source includes a complete fulfillment flow after production. It does not send notifications, process payments, book couriers or infer delivery eligibility. Apply migration `2d114ca5262f` and deploy the matching frontend/API before using it on a hosted release.

## Workflow

1. Start production against the accepted revision. An owner or operator can mark that exact work finished. Finished work cannot restart or receive new automated order-change analyses.
2. The owner opens **Handover**, enters a collection address, instructions and collection window, and chooses the delivery policy. Preparing handover can also mark started work finished. Stable address/delivery settings become defaults for future orders; this order retains its own snapshot, including the handover timezone.
3. **Create customer link** prepares a private link and message to copy into the business's usual communication channel. Nothing is sent by the application. A replacement link revokes the older link without losing customer choices.
4. The customer chooses collection at a future time within the offered window, or requests delivery with an address and contact number. Collection is free. The business checks delivery eligibility and prepares the fee; the customer must explicitly accept the exact address, fee and notes.
5. The owner records any remaining money actually received elsewhere. The app shows the agreed order total, additional delivery fee and recorded payments separately. Sample orders require only fictional receipts; no money changes hands in the demo.
6. With the full balance recorded and no hold, the owner marks **Collected**, or **Out for delivery** followed by **Delivered**. Completion appears in order history and removes the order from the active production queue. Consumed stock is never returned to availability merely because a customer received the product.

If a collection window is missed, the owner can explicitly **Update collection window** before dispatch/completion. A previous collection confirmation is cleared and the customer must choose again on their existing active link. Delivery terms, prices and receipts are preserved. Expired links can be replaced separately. The saved handover timezone stays consistent even if workspace settings change later.

## Delivery policy

| Policy | Additional fee | Confirmation |
| --- | --- | --- |
| Collection only | Delivery unavailable | Customer confirms collection |
| Free / already included | Zero | Business checks address; customer accepts the zero-fee delivery terms |
| Fixed fee | Exact saved integer-cent fee | Business checks address; customer accepts the fee |
| Manual quote | Owner supplies the fee after checking the address | Customer accepts the exact quoted fee |

The business describes its delivery area. This description is not a geocoding or postcode service, and requesting delivery is not a promise of availability. An unsupported address can be changed, or the customer can choose collection before dispatch. Fixed/included pricing cannot be silently overwritten. Once recorded payment exceeds the original order total, changing arrangements requires contacting the business to resolve payment/refund implications; the app does not issue refunds.

## Access and consistency

- Handover is bound to one order and its accepted production revision, with a separate `handovers` record and hash-only `handover_links`.
- Owners manage pricing, receipts and completion. Operators can finish production and see the existing redacted ticket; they cannot read customer delivery details or handover finances.
- Customer links expire after 14 days or the sample workspace's access expiry, whichever comes first. Workspace archive and reviewer-access expiry/rotation disable them as well. Tokens are absent from database history and GET payloads; protect the link like any private customer capability.
- All mutations lock the order first. Version checks reject stale choices, quotes and dispatches. A quote hash binds customer acceptance to the exact address, phone, fee, note and production revision. Changing a choice clears the earlier quote and consent.
- Payment receipts use the existing idempotent ledger, with an outstanding-balance check under the order lock. Collection/dispatch cannot bypass confirmation, full payment or a hold.
- There are no model calls in handover operations. Already-queued order analyses cannot reserve paid inference after production begins.

## Verification

`backend/tests/test_handover.py` exercises authenticated HTTP workflows, consent, prices, expiry, role boundaries and completion. The PostgreSQL companion tests serialize simultaneous choice/dispatch and payment operations in isolated schemas. Browser verification must additionally exercise copy/link entry, customer choice, quote acceptance, receipts and both completion branches at desktop/mobile widths. Passing local checks is not proof that a particular hosted release has been deployed.

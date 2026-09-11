import { DateTime } from 'luxon';
import { ApiError, errorMessage } from './api';
import { money } from './format';
import type { Handover, HandoverDefaults, HandoverStatus } from './types';

export const handoverLabels: Record<HandoverStatus, string> = {
  awaiting_choice: 'Awaiting customer choice',
  delivery_requested: 'Delivery address to review',
  quote_ready: 'Delivery quote awaiting approval',
  confirmed: 'Handover confirmed',
  out_for_delivery: 'Out for delivery',
  collected: 'Collected',
  delivered: 'Delivered',
};

export function handoverLabel(handover: Handover) {
  if (handover.status === 'confirmed')
    return handover.method === 'collection' ? 'Collection confirmed' : 'Delivery confirmed';
  return handoverLabels[handover.status];
}

export function deliveryDescription(config: HandoverDefaults, currency: string) {
  switch (config.delivery_mode) {
    case 'included':
      return 'No extra delivery charge';
    case 'fixed':
      return `${money(config.delivery_fee_cents, currency)} delivery fee`;
    case 'quote':
      return 'Delivery fee quoted after address review';
    default:
      return 'Collection only';
  }
}

export function handoverError(error: unknown) {
  const message = errorMessage(error);
  return error instanceof ApiError && error.status === 409
    ? `${message} Reload the latest handover details before trying again.`
    : message;
}

export function centsFromInput(value: string) {
  if (!/^\d+(\.\d{1,2})?$/.test(value.trim()))
    throw new Error('Enter a positive amount with no more than two decimal places.');
  const [whole, fraction = ''] = value.trim().split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(cents)) throw new Error('The amount is too large.');
  return cents;
}

export function collectionISO(value: string, timezone: string) {
  const date = DateTime.fromISO(value, { zone: timezone });
  if (!date.isValid || date.toFormat("yyyy-MM-dd'T'HH:mm") !== value)
    throw new Error('Enter a valid collection time in the business timezone.');
  return date.toISO()!;
}

export function money(cents: number | null | undefined, currency = 'USD') {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: Number(cents ?? 0) % 100 === 0 ? 0 : 2,
  }).format(Number(cents ?? 0) / 100);
}

export function dateTime(value: string | null | undefined, timezone?: string, includeYear = false) {
  if (!value) return 'Not yet specified';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const zone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? timezone : undefined;
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: includeYear ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
    timeZone: zone,
  }).format(parsed);
}

export function shortDate(value: string | null | undefined) {
  if (!value) return 'Just now';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

export function titleCase(value: string | null | undefined) {
  return (value ?? '').replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

export function initials(value: string) {
  return (
    value
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join('') || 'O'
  );
}

export function pickupInput(value: string | undefined, timezone: string) {
  if (!value) return '';
  const date = DateTime.fromISO(value, { zone: timezone }).setZone(timezone);
  return date.isValid ? date.toFormat("yyyy-MM-dd'T'HH:mm") : '';
}

export function pickupISO(value: string, timezone: string) {
  const date = DateTime.fromISO(value, { zone: timezone });
  if (!date.isValid || date.toFormat("yyyy-MM-dd'T'HH:mm") !== value)
    throw new Error(
      'Enter a valid pickup time in the business timezone. This time may fall within a daylight-saving clock change.',
    );
  return date.toISO()!;
}
import { DateTime } from 'luxon';

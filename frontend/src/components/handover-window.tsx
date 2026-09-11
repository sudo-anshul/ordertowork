import { CalendarClock } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { post } from '../lib/api';
import { pickupInput } from '../lib/format';
import { collectionISO, handoverError } from '../lib/handover';
import { useAction } from '../lib/hooks';
import type { Handover, HandoverResponse } from '../lib/types';
import { Button, ErrorNotice, Field, Modal, Notice } from './ui';

export function HandoverWindowModal({
  endpoint,
  initial,
  latest,
  timezone,
  onClose,
  onReload,
  onSaved,
}: {
  endpoint: string;
  initial: Handover;
  latest: Handover;
  timezone: string;
  onClose: () => void;
  onReload: () => void;
  onSaved: (data: HandoverResponse) => Promise<void>;
}) {
  const [start, setStart] = useState(pickupInput(initial.config.collection_window_start, timezone));
  const [end, setEnd] = useState(pickupInput(initial.config.collection_window_end, timezone));
  const [confirmed, setConfirmed] = useState(false);
  const action = useAction();
  const stale = latest.version !== initial.version;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!confirmed || stale) return;
    void action.run(async () => {
      try {
        const startsAt = collectionISO(start, timezone);
        const endsAt = collectionISO(end, timezone);
        if (new Date(endsAt) <= new Date(startsAt))
          throw new Error('The collection window must end after it starts.');
        return await post<HandoverResponse>(`${endpoint}/window`, {
          expected_version: initial.version,
          collection_window_start: startsAt,
          collection_window_end: endsAt,
        });
      } catch (e) {
        throw new Error(handoverError(e));
      }
    }, onSaved);
  };
  return (
    <Modal title="Update the collection window" onClose={onClose}>
      <form className="modal-body form-stack" onSubmit={submit}>
        <Notice
          tone={
            initial.method === 'collection' && initial.status === 'confirmed' ? 'warning' : 'info'
          }
          title="Keep the customer’s collection choice explicit."
        >
          Any confirmed collection time will be cleared so the customer can choose again. The
          collection address, delivery rules and any delivery agreement stay in place.
        </Notice>
        <Field label="Collection available from" htmlFor="handover-window-start">
          <input
            id="handover-window-start"
            type="datetime-local"
            required
            value={start}
            disabled={action.pending}
            onChange={(event) => setStart(event.target.value)}
          />
        </Field>
        <Field
          label="Collection available until"
          htmlFor="handover-window-end"
          hint={`Times in ${timezone.replaceAll('_', ' ')}. Choose an upcoming window ending within 30 days.`}
        >
          <input
            id="handover-window-end"
            type="datetime-local"
            required
            min={start}
            value={end}
            disabled={action.pending}
            onChange={(event) => setEnd(event.target.value)}
          />
        </Field>
        <label className="checkbox-line">
          <input
            type="checkbox"
            required
            checked={confirmed}
            disabled={action.pending || stale}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span>
            I will let the customer know about the new window and ask them to confirm collection
            again if needed.
          </span>
        </label>
        {stale && (
          <Notice tone="warning" title="This handover has changed.">
            Reload its latest details before changing the window.
            <Button type="button" variant="ghost" onClick={onReload}>
              Reload handover
            </Button>
          </Notice>
        )}
        <ErrorNotice message={action.error} retry={onReload} />
        <div className="form-actions">
          <Button type="button" onClick={onClose} disabled={action.pending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            busy={action.pending}
            disabled={!confirmed || stale || latest.on_hold}
          >
            <CalendarClock size={16} /> Update collection window
          </Button>
        </div>
        <p className="field-hint">
          The customer’s existing link shows the new window. No notification is sent automatically.
        </p>
      </form>
    </Modal>
  );
}

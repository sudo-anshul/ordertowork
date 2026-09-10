import { Download, Paperclip, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { shortDate } from '../lib/format';
import { useAction, useApi } from '../lib/hooks';
import { Badge, Button, ErrorNotice, Loading, Panel, PanelHeading } from './ui';

interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}
export function Attachments({ orderPath }: { orderPath: string }) {
  const path = `${orderPath}/files`;
  const { data, loading, error, refresh } = useApi<{ files: Attachment[] }>(path);
  const input = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const action = useAction();
  const upload = () => {
    if (!selected) return;
    if (selected.size > 5 * 1024 * 1024) {
      setLocalError('Choose a file smaller than 5 MB.');
      return;
    }
    setLocalError(null);
    const form = new FormData();
    form.append('file', selected);
    void action.run(
      () => api<Attachment>(path, { method: 'POST', body: form }),
      async () => {
        setSelected(null);
        if (input.current) input.current.value = '';
        await refresh();
      },
    );
  };
  return (
    <Panel className="panel-padded section-gap">
      <PanelHeading title="Order attachments">
        <Badge>{data?.files.length ?? 0} files</Badge>
      </PanelHeading>
      <p className="settings-description">
        Keep artwork and source documents with the order. Uploading a file stores it; it does not
        change approved terms or run document extraction.
      </p>
      <ErrorNotice message={localError || error || action.error} />
      {loading ? (
        <Loading compact label="Loading attachments…" />
      ) : (
        data?.files.map((file) => (
          <div className="attachment-row" key={file.id}>
            <Paperclip size={17} />
            <div>
              <strong>{file.filename}</strong>
              <small>
                {Math.ceil(file.size_bytes / 1024)} KB · {shortDate(file.created_at)}
              </small>
            </div>
            <a href={`/api${path}/${file.id}/download`} className="subtle-link">
              <Download size={14} /> Download
            </a>
          </div>
        ))
      )}
      <div className="upload-row">
        <label className="sr-only" htmlFor="attachment-file">
          Choose order attachment
        </label>
        <input
          id="attachment-file"
          ref={input}
          type="file"
          className="file-input"
          accept="image/png,image/jpeg,application/pdf"
          onChange={(e) => {
            setSelected(e.target.files?.[0] ?? null);
            setLocalError(null);
          }}
        />
        <Button onClick={upload} disabled={!selected} busy={action.pending}>
          <Upload size={14} /> Upload file
        </Button>
      </div>
      <p className="field-hint">PNG, JPEG, or PDF · up to 5 MB</p>
    </Panel>
  );
}

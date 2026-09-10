import { ShieldCheck, UserPlus, Users } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useWorkspace } from '../lib/workspace';
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Notice,
  PageIntro,
  Panel,
} from '../components/ui';
import { useAuth } from '../lib/auth';
import { del, patch, post } from '../lib/api';
import { initials } from '../lib/format';
import { useAction, useApi } from '../lib/hooks';
import type { Member, WorkspaceRole } from '../lib/types';

export function TeamPage() {
  const workspace = useWorkspace();
  const { session, refresh: refreshSession } = useAuth();
  const path = `/workspaces/${workspace.id}/members`;
  const { data, loading, error, refresh } = useApi<{ members: Member[] }>(path);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const action = useAction();
  const owners = data?.members.filter((member) => member.role === 'owner').length ?? 0;
  const updated = async () => {
    setAdding(false);
    setEditing(null);
    setRemoving(null);
    await refresh();
    await refreshSession();
  };
  return (
    <>
      <PageIntro
        eyebrow="Business access"
        title="The right view for each person."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <UserPlus size={17} /> Add team member
          </Button>
        }
      >
        <p>
          Owners manage agreements and business rules. Operators receive released work and record
          production starts.
        </p>
      </PageIntro>
      <ErrorNotice message={error} retry={() => void refresh()} />
      {loading ? (
        <Loading label="Loading team access…" />
      ) : (
        <Panel>
          {data?.members.map((member) => {
            const finalOwner = member.role === 'owner' && owners <= 1;
            return (
              <div className="member-row" key={member.id}>
                <span className="avatar">{initials(member.name || member.email)}</span>
                <div>
                  <strong>
                    {member.name || member.email}
                    {member.user_id === session?.user.id && ' (you)'}
                  </strong>
                  <small>{member.email}</small>
                </div>
                <Badge tone={member.role === 'owner' ? 'green' : 'neutral'}>
                  {member.role === 'owner' ? 'Owner' : 'Operator'}
                </Badge>
                <div className="member-actions">
                  <Button
                    onClick={() => setEditing(member)}
                    disabled={finalOwner}
                    title={finalOwner ? 'A workspace must retain at least one owner.' : undefined}
                  >
                    Change role
                  </Button>
                  <Button onClick={() => setRemoving(member)} disabled={finalOwner} variant="ghost">
                    Remove access
                  </Button>
                </div>
              </div>
            );
          })}
        </Panel>
      )}
      <div className="role-explanation">
        <section>
          <h3>
            <ShieldCheck size={17} /> Owner
          </h3>
          <p>
            Review customer messages and proposals, manage prices and resources, record received
            deposits, control holds, and manage the team.
          </p>
        </section>
        <section>
          <h3>
            <Users size={17} /> Production operator
          </h3>
          <p>
            Open released work tickets and start production. Customer messages, email addresses,
            prices, deposit balances, and business settings stay restricted.
          </p>
        </section>
      </div>
      {(adding || editing) && (
        <MemberModal
          path={path}
          member={editing}
          onClose={() => {
            setAdding(false);
            setEditing(null);
          }}
          onSaved={updated}
        />
      )}
      {removing && (
        <Modal title="Remove workspace access?" onClose={() => setRemoving(null)}>
          <div className="modal-body">
            <p>
              <strong>{removing.name || removing.email}</strong> will no longer be able to open this
              business workspace. Existing order records remain.
            </p>
            <ErrorNotice message={action.error} />
            <div className="form-actions">
              <Button onClick={() => setRemoving(null)}>Cancel</Button>
              <Button
                variant="danger"
                busy={action.pending}
                onClick={() => void action.run(() => del(`${path}/${removing.id}`), updated)}
              >
                Remove access
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

function MemberModal({
  path,
  member,
  onClose,
  onSaved,
}: {
  path: string;
  member: Member | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [email, setEmail] = useState(member?.email ?? '');
  const [role, setRole] = useState<WorkspaceRole>(member?.role ?? 'operator');
  const action = useAction();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void action.run(
      () =>
        member
          ? patch(`${path}/${member.id}`, { role })
          : post(path, { email: email.trim(), role }),
      onSaved,
    );
  };
  return (
    <Modal title={member ? 'Change team role' : 'Add a team member'} onClose={onClose}>
      <form onSubmit={submit} className="modal-body form-stack">
        {!member && (
          <Notice title="Ask the person to sign in once first.">
            Add their existing account by email. This action grants workspace access; it does not
            send an invitation email.
          </Notice>
        )}
        <Field label="Account email" htmlFor="member-email">
          <input
            id="member-email"
            type="email"
            autoFocus
            required
            disabled={Boolean(member)}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Workspace role" htmlFor="member-role">
          <select
            id="member-role"
            value={role}
            onChange={(e) => setRole(e.target.value as WorkspaceRole)}
          >
            <option value="operator">Production operator</option>
            <option value="owner">Owner</option>
          </select>
        </Field>
        <p className="field-hint">
          {role === 'owner'
            ? 'Owners can view customer and financial records and manage team access.'
            : 'Operators can view released work and record production starts.'}
        </p>
        <ErrorNotice message={action.error} />
        <div className="form-actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" busy={action.pending}>
            {member ? 'Save role' : 'Add member'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

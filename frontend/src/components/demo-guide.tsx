import { ChevronDown, Clock3 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { post } from '../lib/api';
import { sampleDestination } from '../lib/demo';
import { useAction } from '../lib/hooks';
import type { Session } from '../lib/types';
import { Badge, Button, ErrorNotice } from './ui';

export function DemoGuide() {
  const { session, acceptSession } = useAuth();
  const navigate = useNavigate();
  const reset = useAction();
  const [now, setNow] = useState(Date.now());
  const [expanded, setExpanded] = useState(() => window.matchMedia('(min-width: 781px)').matches);
  useEffect(() => {
    if (!session?.demo) return;
    const interval = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(interval);
  }, [session?.demo]);
  if (session?.auth_method === 'reviewer' && session.reviewer) {
    const expires = new Date(session.reviewer.expires_at).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
    return (
      <section className="demo-guide" aria-label="Reviewer guide">
        <div className="demo-guide-content">
          <div className="section-title">
            <Badge tone="green">Reviewer access</Badge>
            <strong>Follow a customer change through to work.</strong>
          </div>
          <ol aria-label="Suggested reviewer walkthrough">
            <li>
              <span>01</span> Add a customer message
            </li>
            <li>
              <span>02</span> Compare checked options
            </li>
            <li>
              <span>03</span> Approve the exact revision
            </li>
            <li>
              <span>04</span> Record a sample deposit &amp; start work
            </li>
          </ol>
          <p>
            These sample businesses are yours to explore through {expires}. Live analyses are
            separate from public demo allowances. Prepared proposals use reference rules; a new
            message runs a fresh analysis. Its execution record identifies the model and tool
            checks.
          </p>
          <p>
            Use fictional data. You can create orders, adjust business rules and upload sample
            files. Start fresh examples whenever you want to repeat the walkthrough.
          </p>
          <Button
            busy={reset.pending}
            onClick={() =>
              void reset.run(async () => {
                const next = await post<Session>('/auth/reviewer/reset');
                const destination = await sampleDestination(next);
                acceptSession(next);
                navigate(destination);
              })
            }
          >
            Start fresh examples
          </Button>
          <ErrorNotice message={reset.error} />
        </div>
      </section>
    );
  }
  if (session?.auth_method !== 'demo' || !session.demo) return null;
  const minutes = Math.max(0, Math.ceil((Date.parse(session.demo.expires_at) - now) / 60000));
  return (
    <details
      className="demo-guide"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        <span className="demo-guide-title">
          <Badge tone="green">Live demo</Badge>
          <strong>Follow a customer change through to work.</strong>
        </span>
        <time className="demo-guide-time" dateTime={session.demo.expires_at}>
          <Clock3 size={13} aria-hidden="true" /> {minutes} min left
        </time>
        <ChevronDown size={15} className="demo-guide-chevron" aria-hidden="true" />
      </summary>
      <div className="demo-guide-content">
        <ol aria-label="Suggested demo walkthrough">
          <li>
            <span>01</span> Read the customer’s change
          </li>
          <li>
            <span>02</span> Compare workable options
          </li>
          <li>
            <span>03</span> Open the approval link
          </li>
          <li>
            <span>04</span> Record deposit &amp; start work
          </li>
        </ol>
        <p>
          Your sample data is private to this session. Switch businesses to explore both examples.
          The prepared proposals use reference rules; opening them makes no AI call. To try a new
          check, choose <strong>Add customer message</strong> and enter a fictional request. Open
          its execution record to see the model, tool checks and token usage.
        </p>
        <p>
          Up to {session.demo.max_agent_jobs_per_workspace} new analysis attempts per business,
          including retries, subject to service availability.
        </p>
      </div>
    </details>
  );
}

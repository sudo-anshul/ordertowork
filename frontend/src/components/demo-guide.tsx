import { ChevronDown, Clock3 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useAuth } from '../lib/auth';
import { Badge } from './ui';

export function DemoGuide() {
  const { session } = useAuth();
  const [now, setNow] = useState(Date.now());
  const [expanded, setExpanded] = useState(() => window.matchMedia('(min-width: 781px)').matches);
  useEffect(() => {
    if (!session?.demo) return;
    const interval = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(interval);
  }, [session?.demo]);
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
          Saved analysis uses reference rules; new AI requests depend on service availability (
          {session.demo.max_agent_jobs_per_workspace} per business).
        </p>
      </div>
    </details>
  );
}

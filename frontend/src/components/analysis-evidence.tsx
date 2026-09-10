import { Check, ChevronDown, CircleAlert } from 'lucide-react';
import type { Analysis, AnalysisJob } from '../lib/types';
import { money, titleCase } from '../lib/format';
import { Badge } from './ui';

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function sourceQuotes(evidence: Analysis['evidence'] | undefined): string[] {
  if (typeof evidence === 'string') return evidence.trim() ? [evidence] : [];
  if (Array.isArray(evidence))
    return evidence.flatMap((item) => {
      if (typeof item === 'string') return [item];
      const value = record(item);
      return typeof value.quote === 'string'
        ? [value.quote]
        : typeof value.text === 'string'
          ? [value.text]
          : [];
    });
  return Object.entries(evidence ?? {}).flatMap(([key, value]) =>
    typeof value === 'string' ? [`${titleCase(key)}: ${value}`] : [],
  );
}

export function AnalysisEvidence({
  job,
  analysis,
}: {
  job: AnalysisJob;
  analysis?: Analysis | null;
}) {
  const events = job.tool_events ?? [];
  const usage = record(
    [...events].reverse().find((event) => event.tool === 'bedrock_usage')?.result,
  );
  const modelId = typeof usage.model_id === 'string' ? usage.model_id : null;
  const modelName = modelId === 'qwen.qwen3-235b-a22b-2507' ? 'Qwen3 235B' : modelId;
  const provider = usage.endpoint === 'mantle' ? 'Bedrock Mantle' : 'Bedrock';
  const quotes = sourceQuotes(analysis?.evidence);
  const labels: Record<string, string> = {
    read_order_context: 'Read order facts',
    preview_change: 'Check stock, capacity and price',
    reference_interpreter: 'Reference interpretation',
    bedrock_usage: 'AI usage',
  };
  const mode =
    job.mode === 'bedrock'
      ? `Strands + ${provider}${modelName ? ` · ${modelName}` : ''}`
      : job.mode === 'reference'
        ? 'Deterministic reference'
        : 'Mode not recorded';
  return (
    <details className="analysis-evidence">
      <summary>
        <span>
          <strong>
            {job.status === 'succeeded' ? 'Request check complete' : 'Analysis execution record'}
          </strong>
          <small>
            {mode} · {events.length} recorded {events.length === 1 ? 'event' : 'events'}
          </small>
        </span>
        <Badge tone={job.status === 'succeeded' ? 'green' : 'amber'}>{titleCase(job.status)}</Badge>
        <ChevronDown size={15} aria-hidden="true" />
      </summary>
      <div className="analysis-evidence-body">
        <p className="analysis-mode-note">
          {job.mode === 'reference'
            ? 'This run used deterministic reference interpretation. No AI model was called.'
            : job.mode === 'bedrock'
              ? `This run was configured for Strands with ${provider}. The model interprets the message; server tools check order facts, stock, capacity and price. The events below show what ran. Owner review and customer approval still control changes.`
              : 'The server did not record the execution mode for this run.'}
        </p>
        {events.length ? (
          <ol className="tool-events">
            {events.map((event, index) => {
              const result = record(event.result);
              const terms = record(result.terms);
              const issues = Array.isArray(result.issues)
                ? result.issues.flatMap((issue) =>
                    typeof record(issue).message === 'string'
                      ? [String(record(issue).message)]
                      : [],
                  )
                : [];
              return (
                <li key={`${event.tool}-${index}`}>
                  <span className="tool-event-index">{index + 1}</span>
                  <div>
                    <strong>{labels[event.tool] ?? titleCase(event.tool)}</strong>
                    {event.tool === 'read_order_context' && (
                      <p>Read the saved order and business facts.</p>
                    )}
                    {event.tool === 'bedrock_usage' && (
                      <>
                        {typeof result.model_id === 'string' && (
                          <p>
                            Model: <code>{result.model_id}</code>
                            {result.endpoint === 'mantle' && ' · Bedrock Mantle'}
                          </p>
                        )}
                        <p>
                          {typeof result.input_tokens === 'number'
                            ? result.input_tokens.toLocaleString()
                            : 'Unreported'}{' '}
                          input tokens ·{' '}
                          {typeof result.output_tokens === 'number'
                            ? result.output_tokens.toLocaleString()
                            : 'unreported'}{' '}
                          output tokens
                        </p>
                        {result.usage_complete === false && (
                          <p>Usage may be incomplete because the request did not finish.</p>
                        )}
                      </>
                    )}
                    {event.tool === 'reference_interpreter' &&
                      typeof result.intent === 'string' && (
                        <p>Recognized: {titleCase(result.intent)}.</p>
                      )}
                    {typeof result.feasible === 'boolean' && (
                      <p>
                        {result.feasible
                          ? 'The checked request fits the configured resources.'
                          : 'The checked request needs another option.'}
                      </p>
                    )}
                    {typeof terms.total_cents === 'number' && (
                      <p>
                        Calculated total:{' '}
                        <b>
                          {money(
                            terms.total_cents,
                            typeof terms.currency === 'string' ? terms.currency : 'USD',
                          )}
                        </b>
                        .
                      </p>
                    )}
                    {issues.length > 0 && (
                      <ul>
                        {issues.map((issue, i) => (
                          <li key={i}>{issue}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {event.tool === 'bedrock_usage' && result.usage_complete === false ? (
                    <CircleAlert size={14} aria-label="Incomplete usage record" />
                  ) : (
                    <Check size={14} aria-hidden="true" />
                  )}
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="field-hint">No tool events were recorded for this run.</p>
        )}
        {quotes.length > 0 && (
          <div className="analysis-source-quotes">
            <h3>Source evidence</h3>
            {quotes.map((quote, index) => (
              <blockquote key={index}>{quote}</blockquote>
            ))}
          </div>
        )}
        {Boolean(analysis?.missing_fields?.length) && (
          <div className="analysis-missing">
            <h3>Details still needed</h3>
            <ul>
              {analysis?.missing_fields.map((field) => (
                <li key={field}>{titleCase(field)}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

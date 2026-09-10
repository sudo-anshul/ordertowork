import { Component } from 'react';
import type { ReactNode } from 'react';
import { Brand, Button, EmptyState } from './ui';

export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="standalone">
        <Brand />
        <EmptyState
          title="This view needs to be reloaded."
          action={
            <Button variant="primary" onClick={() => window.location.reload()}>
              Reload the current view
            </Button>
          }
        >
          An unexpected display error interrupted the page. Reload to retrieve the current saved
          state from the server.
        </EmptyState>
      </div>
    );
  }
}

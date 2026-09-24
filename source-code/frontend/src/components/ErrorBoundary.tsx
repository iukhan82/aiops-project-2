import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "./Button";

interface State {
  error: Error | null;
}

/** A screen that throws while rendering shows what failed and how to recover, instead of a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Screen failed to render", error, info.componentStack);
  }

  componentDidUpdate(previous: { resetKey?: string }): void {
    if (this.state.error && previous.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="state state-error" data-testid="screen-error">
        <h2>This screen failed to draw</h2>
        <p>Something in this screen went wrong. Nothing you entered or approved was lost: the failure happened while showing data.</p>
        <p className="muted">
          Detail: <code>{this.state.error.message}</code>
        </p>
        <Button onClick={() => this.setState({ error: null })}>Try again</Button>
      </div>
    );
  }
}

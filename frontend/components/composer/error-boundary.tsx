"use client";

import { Component, type ReactNode } from "react";
import { EmptyState } from "./empty-state";
import { Button } from "@/components/ui/button";

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  // Explicit constructor to declare initial state without triggering TS4114
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: undefined };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  override componentDidCatch(error: Error) {
    console.error("[ErrorBoundary]", error);
  }

  override render() {
    if (this.state.hasError) {
      return (
        <EmptyState
          title="Something went wrong"
          description={this.state.error?.message ?? "Try refreshing the page."}
          action={
            <Button onClick={() => this.setState({ hasError: false, error: undefined })}>
              Retry
            </Button>
          }
        />
      );
    }
    return this.props.children;
  }
}

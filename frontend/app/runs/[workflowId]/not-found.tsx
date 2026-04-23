import { EmptyState } from "@/components/composer/empty-state";

export default function NotFound() {
  return (
    <EmptyState
      title="Workflow not found"
      description="This workflow either doesn't exist or you don't have access to it."
    />
  );
}

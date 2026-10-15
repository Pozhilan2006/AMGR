import { EmptyState } from "@/components/ui/empty-state";

export function GraphPlaceholder() {
  return (
    <EmptyState
      state="integration"
      title="Graph data is not available"
      description="The graph API is present but returns 501 Not Implemented. No nodes, relationships, or security states are being simulated."
    />
  );
}

import { EmptyState } from "@/components/ui/empty-state";

export function PolicyVersionHistory({ policyId }: { policyId: string }) {
  return <EmptyState state="integration" title="Version history unavailable" description={`The backend has no policy version-history route for ${policyId}. No versions or metadata are being inferred.`} />;
}

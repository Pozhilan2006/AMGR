import { PageHeader } from "@/components/layout/page-header";
import { EmptyState } from "@/components/ui/empty-state";

export function FeaturePlaceholder({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="page-content">
      <PageHeader title={title} description="Read-only dashboard surface." />
      <EmptyState state="integration" description={description} />
    </div>
  );
}
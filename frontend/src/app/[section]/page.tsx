import { notFound } from "next/navigation";

import { FeaturePlaceholder } from "@/components/feature-placeholder";

const sections: Record<string, { title: string; description: string }> = {
  "flow-graph": {
    title: "Flow graph",
    description: "Graph persistence, traversal, and visualization are not implemented yet.",
  },
  policies: {
    title: "Policies",
    description: "Policy lifecycle and deterministic evaluation are not implemented yet.",
  },
  decisions: {
    title: "Decisions",
    description: "Authorization decision history is not implemented yet.",
  },
  approvals: {
    title: "Approvals",
    description: "Approval workflows are not implemented yet.",
  },
  "security-operator": {
    title: "AI Security Operator",
    description: "Natural-language policy proposals and provider integration are not implemented yet.",
  },
};

export default async function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  const feature = sections[section];

  if (!feature) notFound();

  return <FeaturePlaceholder title={feature.title} description={feature.description} />;
}